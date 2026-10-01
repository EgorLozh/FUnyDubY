"""Назначение реплик участникам: захват с TTL, освобождение, массовый захват.

Гонка «двое взяли одну реплику» решается на уровне БД, а не в приложении: у `assignments`
первичный ключ — `line_id`, поэтому второй захват физически не может создать вторую строку.
Проверка «свободна ли» и вставка идут **одним** `INSERT ... ON CONFLICT DO NOTHING`, так что
между чтением и записью нет окна (в отличие от «проверил → вставил» в коде).

Отдельно поддержан просроченный захват: участник, закрывший вкладку, не должен блокировать
реплику навсегда, поэтому захват с истёкшим `expires_at` перехватывается тем же запросом.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, Forbidden, NotFound
from app.models import Assignment, DialogueLine, Participant
from app.services.rooms import emit_event


def _now() -> datetime:
    return datetime.now(UTC)


def _expiry() -> datetime:
    return _now() + timedelta(minutes=settings.assignment_ttl_minutes)


def _active_filter(now: datetime):
    """Захват считается живым, если срок не задан или ещё не истёк."""
    return or_(Assignment.expires_at.is_(None), Assignment.expires_at > now)


async def _ensure_line(session: AsyncSession, room_id: str, line_id: uuid.UUID) -> DialogueLine:
    line = (
        await session.execute(
            select(DialogueLine).where(
                DialogueLine.id == line_id, DialogueLine.room_id == room_id
            )
        )
    ).scalar_one_or_none()
    if line is None:
        raise NotFound("Реплика не найдена", code="line_not_found")
    return line


async def _ensure_participant(
    session: AsyncSession, room_id: str, participant_id: uuid.UUID
) -> Participant:
    participant = (
        await session.execute(
            select(Participant).where(
                Participant.id == participant_id, Participant.room_id == room_id
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise NotFound("Участник не найден в этой комнате", code="participant_not_found")
    return participant


async def room_assignments(
    session: AsyncSession, room_id: str
) -> dict[uuid.UUID, Assignment]:
    """Все назначения комнаты одним запросом — чтобы список реплик не делал N+1."""
    rows = (
        (
            await session.execute(
                select(Assignment)
                .join(DialogueLine, DialogueLine.id == Assignment.line_id)
                .where(DialogueLine.room_id == room_id)
            )
        )
        .scalars()
        .all()
    )
    return {row.line_id: row for row in rows}


async def get_assignment(session: AsyncSession, line_id: uuid.UUID) -> Assignment | None:
    return (
        await session.execute(select(Assignment).where(Assignment.line_id == line_id))
    ).scalar_one_or_none()


async def participant_names(session: AsyncSession, room_id: str) -> dict[uuid.UUID, str]:
    """Имена участников комнаты — чтобы в списке реплик показывать «кто взял», а не UUID."""
    rows = (
        await session.execute(
            select(Participant.id, Participant.display_name).where(Participant.room_id == room_id)
        )
    ).all()
    return {row.id: row.display_name for row in rows}


async def claim(
    session: AsyncSession,
    room_id: str,
    line_id: uuid.UUID,
    participant_id: uuid.UUID,
    *,
    expected_version: int | None = None,
) -> tuple[Assignment, bool]:
    """Взять реплику. Возвращает (назначение, создано_заново).

    Бросает 409 `line_taken`, если реплика живьём занята другим участником.
    """
    line = await _ensure_line(session, room_id, line_id)
    if expected_version is not None and line.version != expected_version:
        raise Conflict(
            "Реплика изменилась — обновите список",
            code="version_conflict",
            extra={"version": line.version},
        )
    await _ensure_participant(session, room_id, participant_id)

    now = _now()
    # Шаг 1: забрать себе уже существующую строку, если она моя или просрочена.
    takeover = (
        await session.execute(
            update(Assignment)
            .where(
                Assignment.line_id == line_id,
                or_(
                    Assignment.participant_id == participant_id,
                    Assignment.expires_at.is_not(None) & (Assignment.expires_at < now),
                ),
            )
            .values(
                participant_id=participant_id,
                assigned_at=now,
                expires_at=_expiry(),
                version=Assignment.version + 1,
            )
            .returning(Assignment.line_id)
        )
    ).first()

    created = False
    if takeover is None:
        # Шаг 2: строки нет — вставляем, полагаясь на PRIMARY KEY по line_id.
        inserted = (
            await session.execute(
                pg_insert(Assignment)
                .values(
                    line_id=line_id,
                    participant_id=participant_id,
                    assigned_at=now,
                    expires_at=_expiry(),
                    version=1,
                )
                .on_conflict_do_nothing(index_elements=["line_id"])
                .returning(Assignment.line_id)
            )
        ).first()
        if inserted is None:
            holder = await get_assignment(session, line_id)
            holder_participant = (
                await session.execute(
                    select(Participant).where(Participant.id == holder.participant_id)
                )
            ).scalar_one_or_none() if holder else None
            raise Conflict(
                "Реплика уже занята другим участником",
                code="line_taken",
                extra={
                    "participant_id": str(holder.participant_id) if holder else None,
                    "display_name": holder_participant.display_name if holder_participant else None,
                },
            )
        created = True

    assignment = await get_assignment(session, line_id)
    if assignment is None:  # pragma: no cover — невозможное состояние
        raise Conflict("Не удалось закрепить реплику", code="assignment_failed")

    payload: dict[str, Any] = {
        "line_id": str(line_id),
        "participant_id": str(participant_id),
        "version": assignment.version,
    }
    await emit_event(session, room_id, "assignment.changed", payload)
    return assignment, created


async def release(
    session: AsyncSession,
    room_id: str,
    line_id: uuid.UUID,
    *,
    participant_id: uuid.UUID,
    force: bool = False,
) -> None:
    """Освободить реплику. Участник может снять только свой захват."""
    await _ensure_line(session, room_id, line_id)
    assignment = await get_assignment(session, line_id)
    if assignment is None:
        return
    if assignment.participant_id != participant_id and not force:
        raise Forbidden("Реплику занимал другой участник", code="not_your_assignment")
    # Освобождение — это удаление строки: «свободна» = «нет назначения», без неоднозначных
    # состояний вида «строка есть, но просрочена» (их пришлось бы учитывать во всех запросах).
    await session.execute(delete(Assignment).where(Assignment.line_id == line_id))
    await emit_event(
        session,
        room_id,
        "assignment.changed",
        {"line_id": str(line_id), "participant_id": None, "released": True},
    )


async def heartbeat(
    session: AsyncSession, room_id: str, line_id: uuid.UUID, participant_id: uuid.UUID
) -> Assignment:
    """Продлить захват (клиент вызывает, пока участник работает над репликой)."""
    await _ensure_line(session, room_id, line_id)
    assignment = await get_assignment(session, line_id)
    if assignment is None or assignment.participant_id != participant_id:
        raise Conflict("Реплика не закреплена за вами", code="assignment_owner_mismatch")
    await session.execute(
        update(Assignment)
        .where(Assignment.line_id == line_id)
        .values(expires_at=_expiry(), version=Assignment.version + 1)
    )
    await session.commit()
    refreshed = await get_assignment(session, line_id)
    assert refreshed is not None
    return refreshed


async def _candidate_lines(
    session: AsyncSession,
    room_id: str,
    *,
    speaker_key: str | None,
    line_ids: Iterable[uuid.UUID] | None,
    only_unassigned: bool,
) -> list[uuid.UUID]:
    now = _now()
    query = select(DialogueLine.id).where(DialogueLine.room_id == room_id)
    if line_ids is not None:
        ids = list(line_ids)
        query = query.where(DialogueLine.id.in_(ids))
    if speaker_key:
        query = query.where(DialogueLine.speaker_key == speaker_key)
    if only_unassigned:
        busy = select(Assignment.line_id).where(_active_filter(now))
        query = query.where(DialogueLine.id.not_in(busy))
    query = query.order_by(DialogueLine.idx)
    return list((await session.execute(query)).scalars().all())


async def bulk_claim(
    session: AsyncSession,
    room_id: str,
    participant_id: uuid.UUID,
    *,
    speaker_key: str | None = None,
    line_ids: Iterable[uuid.UUID] | None = None,
    scope: str = "all-unassigned",
) -> dict[str, list[uuid.UUID]]:
    """Захватить пачку реплик одной транзакцией.

    Возвращает `captured` (взято сейчас), `already_mine` (было моим) и `busy` (занято другими).
    """
    await _ensure_participant(session, room_id, participant_id)
    if scope not in {"all-unassigned", "my-speaker", "line-ids"}:
        raise Conflict(f"Неизвестный scope: {scope}", code="unknown_scope")

    if scope == "line-ids" and not line_ids:
        raise Conflict("scope=line-ids требует список line_ids", code="line_ids_required")
    if scope == "my-speaker" and not speaker_key:
        raise Conflict("scope=my-speaker требует speaker_key", code="speaker_key_required")

    candidates = await _candidate_lines(
        session,
        room_id,
        speaker_key=speaker_key,
        line_ids=line_ids,
        only_unassigned=(scope == "all-unassigned"),
    )
    if not candidates:
        return {"captured": [], "already_mine": [], "busy": []}

    now = _now()
    expires = _expiry()
    inserted = (
        await session.execute(
            pg_insert(Assignment)
            .values(
                [
                    {
                        "line_id": line_id,
                        "participant_id": participant_id,
                        "assigned_at": now,
                        "expires_at": expires,
                        "version": 1,
                    }
                    for line_id in candidates
                ]
            )
            .on_conflict_do_nothing(index_elements=["line_id"])
            .returning(Assignment.line_id)
        )
    ).scalars().all()
    captured = list(inserted)
    captured_set = set(captured)

    existing = (
        await session.execute(
            select(Assignment).where(Assignment.line_id.in_(candidates))
        )
    ).scalars().all()
    already_mine = [
        row.line_id
        for row in existing
        if row.participant_id == participant_id and row.line_id not in captured_set
    ]
    busy = [
        row.line_id
        for row in existing
        if row.participant_id != participant_id and row.line_id not in captured_set
    ]

    # Продлеваем и свои прежние захваты — участник сейчас работает с этим набором.
    if already_mine:
        await session.execute(
            update(Assignment)
            .where(Assignment.line_id.in_(already_mine))
            .values(expires_at=expires)
        )

    if captured:
        await emit_event(
            session,
            room_id,
            "assignment.bulk",
            {
                "participant_id": str(participant_id),
                "captured": [str(item) for item in captured],
                "scope": scope,
            },
        )
    return {"captured": captured, "already_mine": already_mine, "busy": busy}
