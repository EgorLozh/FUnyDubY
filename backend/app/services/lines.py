"""Реплики диалога: чтение, правки, разделение/склейка, спикеры.

Результаты ML не считаются неизменяемыми: пользователь может поправить текст, спикера и
границы реплики, разделить или склеить реплики. Любая правка увеличивает `version` — на этом
построена оптимистическая блокировка: клиент присылает `expected_version`, и если реплику
уже поправили, получает 409 вместо тихой потери чужих изменений.

Нарезка оригинальной речи (`speech/segments/{line_id}.wav`) пересобирается сразу: именно этот
файл участник слушает перед записью, поэтому он не должен расходиться с текстом и таймкодами.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Iterable, Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, DomainError, NotFound
from app.core.security import room_relative
from app.models import Assignment, DialogueLine, Participant
from app.services import storage
from app.services.rooms import emit_event
from app.video import ffmpeg

MIN_LINE_MS = 100
MAX_LINE_MS = 60_000


class InvalidLineBounds(DomainError):
    status_code = 422
    code = "invalid_line_bounds"


def _speech_source(room_id: str):
    """Дорожка речи, из которой нарезаются фрагменты реплик."""
    path = storage.absolute(room_relative(room_id, "speech", "speech.wav"))
    return path if path.exists() else None


def _segment_path(room_id: str, line_id: uuid.UUID):
    return storage.absolute(room_relative(room_id, "speech", "segments", f"{line_id}.wav"))


async def _recut_segment(room_id: str, line: DialogueLine) -> None:
    """Пересобрать фрагмент речи под текущие границы реплики."""
    source = _speech_source(room_id)
    if source is None:
        return
    target = _segment_path(room_id, line.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    start = max(0, line.start_ms - settings.segment_pad_ms)
    end = line.end_ms + settings.segment_pad_ms
    await asyncio.to_thread(ffmpeg.slice_wav, source, target, start, end)
    line.speech_path = room_relative(room_id, "speech", "segments", f"{line.id}.wav")


def _check_bounds(start_ms: int, end_ms: int) -> None:
    duration = end_ms - start_ms
    if start_ms < 0 or duration < MIN_LINE_MS or duration > MAX_LINE_MS:
        raise InvalidLineBounds(
            f"Недопустимые границы реплики: {start_ms}…{end_ms} мс "
            f"(длительность должна быть {MIN_LINE_MS}…{MAX_LINE_MS} мс)"
        )


async def list_lines(
    session: AsyncSession,
    room_id: str,
    *,
    speaker: str | None = None,
    assigned_to: str | None = None,
    participant_id: uuid.UUID | None = None,
    since_version: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[DialogueLine]:
    query = select(DialogueLine).where(DialogueLine.room_id == room_id)
    if speaker:
        query = query.where(
            or_(DialogueLine.speaker_key == speaker, DialogueLine.speaker_label == speaker)
        )
    if assigned_to in {"me", "unassigned"}:
        query = query.outerjoin(Assignment, Assignment.line_id == DialogueLine.id)
        if assigned_to == "me":
            if participant_id is None:
                raise Conflict("Фильтр assigned_to=me требует токен участника", code="token_required")
            query = query.where(Assignment.participant_id == participant_id)
        else:
            query = query.where(Assignment.line_id.is_(None))
    if since_version is not None:
        query = query.where(DialogueLine.version > since_version)
    query = query.order_by(DialogueLine.idx).offset(offset)
    if limit:
        query = query.limit(limit)
    return list((await session.execute(query)).scalars().all())


async def get_line(session: AsyncSession, room_id: str, line_id: uuid.UUID) -> DialogueLine:
    line = (
        await session.execute(
            select(DialogueLine).where(DialogueLine.id == line_id, DialogueLine.room_id == room_id)
        )
    ).scalar_one_or_none()
    if line is None:
        raise NotFound("Реплика не найдена", code="line_not_found")
    return line


async def list_speakers(session: AsyncSession, room_id: str) -> list[dict]:
    """Сводка по спикерам: ключ, подпись, число реплик и суммарная длительность."""
    rows = (
        await session.execute(
            select(
                DialogueLine.speaker_key,
                DialogueLine.speaker_label,
                func.count().label("lines"),
                func.coalesce(func.sum(DialogueLine.end_ms - DialogueLine.start_ms), 0).label(
                    "total_ms"
                ),
            )
            .where(DialogueLine.room_id == room_id)
            .group_by(DialogueLine.speaker_key, DialogueLine.speaker_label)
        )
    ).all()
    return sorted(
        (
            {
                "speaker_key": row.speaker_key,
                "speaker_label": row.speaker_label,
                "lines": int(row.lines),
                "total_ms": int(row.total_ms),
            }
            for row in rows
        ),
        key=lambda item: item["speaker_label"],
    )


async def update_line(
    session: AsyncSession,
    room_id: str,
    line_id: uuid.UUID,
    *,
    text: str | None = None,
    speaker_label: str | None = None,
    speaker_key: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    expected_version: int | None = None,
) -> DialogueLine:
    """Правка реплики с оптимистической блокировкой."""
    line = await get_line(session, room_id, line_id)
    if expected_version is not None and line.version != expected_version:
        raise Conflict(
            "Реплику уже изменили — обновите данные",
            code="version_conflict",
            details={"version": line.version},
        )

    new_start = line.start_ms if start_ms is None else start_ms
    new_end = line.end_ms if end_ms is None else end_ms
    bounds_changed = (new_start, new_end) != (line.start_ms, line.end_ms)
    if bounds_changed:
        _check_bounds(new_start, new_end)
        await _assert_no_overlap(session, room_id, line, new_start, new_end)

    changed = False
    if text is not None and text != line.text:
        line.text = text
        changed = True
    if speaker_label is not None and speaker_label != line.speaker_label:
        line.speaker_label = speaker_label
        line.speaker_key = speaker_key or _slug(speaker_label)
        changed = True
    if bounds_changed:
        line.start_ms, line.end_ms = new_start, new_end
        line.overlaps = await _has_overlap(session, room_id, line)
        line.words = _filter_words(line.words, new_start, new_end)
        changed = True

    if not changed:
        return line

    line.is_edited = True
    line.version += 1
    if bounds_changed:
        await _recut_segment(room_id, line)
    await session.flush()
    await emit_event(
        session,
        room_id,
        "line.updated",
        {"line_id": str(line.id), "version": line.version, "idx": line.idx},
    )
    return line


async def split_line(
    session: AsyncSession, room_id: str, line_id: uuid.UUID, *, at_ms: int
) -> tuple[DialogueLine, DialogueLine]:
    """Разделить реплику в точке `at_ms` на две."""
    line = await get_line(session, room_id, line_id)
    if not (line.start_ms + MIN_LINE_MS <= at_ms <= line.end_ms - MIN_LINE_MS):
        raise InvalidLineBounds(
            f"Точка разделения {at_ms} мс должна быть внутри реплики "
            f"({line.start_ms + MIN_LINE_MS}…{line.end_ms - MIN_LINE_MS} мс)"
        )

    await _shift_indexes(session, room_id, from_idx=line.idx + 1, delta=1)

    words = line.words or []
    head = [word for word in words if _word_mid(word) < at_ms]
    tail = [word for word in words if _word_mid(word) >= at_ms]

    tail_line = DialogueLine(
        room_id=room_id,
        job_id=line.job_id,
        idx=line.idx + 1,
        start_ms=at_ms,
        end_ms=line.end_ms,
        speaker_key=line.speaker_key,
        speaker_label=line.speaker_label,
        text=_join_words(tail),
        words=tail or None,
        is_short=(line.end_ms - at_ms) < settings.min_line_ms,
        overlaps=line.overlaps,
        is_edited=True,
        version=1,
    )
    session.add(tail_line)

    line.end_ms = at_ms
    line.text = _join_words(head)
    line.words = head or None
    line.is_short = (at_ms - line.start_ms) < settings.min_line_ms
    line.is_edited = True
    line.version += 1

    await session.flush()
    await _recut_segment(room_id, line)
    await _recut_segment(room_id, tail_line)
    # Разделение меняет ответ на «кто что озвучивает» — назначение исходной реплики снимаем.
    from sqlalchemy import delete

    await session.execute(delete(Assignment).where(Assignment.line_id == line.id))
    await session.flush()
    await emit_event(
        session,
        room_id,
        "line.split",
        {"line_id": str(line.id), "new_line_id": str(tail_line.id), "at_ms": at_ms},
    )
    return line, tail_line


async def merge_with_next(
    session: AsyncSession, room_id: str, line_id: uuid.UUID
) -> DialogueLine:
    """Склеить реплику со следующей по порядку."""
    line = await get_line(session, room_id, line_id)
    following = (
        await session.execute(
            select(DialogueLine)
            .where(DialogueLine.room_id == room_id, DialogueLine.idx > line.idx)
            .order_by(DialogueLine.idx)
            .limit(1)
        )
    ).scalar_one_or_none()
    if following is None:
        raise Conflict("Следующей реплики нет — склеивать нечего", code="no_next_line")

    _check_bounds(line.start_ms, following.end_ms)
    merged_text = " ".join(part for part in (line.text.strip(), following.text.strip()) if part)
    line.text = merged_text
    line.words = (line.words or []) + (following.words or [])
    line.end_ms = following.end_ms
    line.speaker_label = following.speaker_label or line.speaker_label
    line.speaker_key = following.speaker_key or line.speaker_key
    line.is_short = False
    line.is_edited = True
    line.version += 1

    # Назначение склеенной реплики терять нельзя: если у «хвоста» был держатель, он переезжает
    # на объединённую реплику (иначе участник молча теряет работу).
    from sqlalchemy import delete

    tail_assignment = (
        await session.execute(select(Assignment).where(Assignment.line_id == following.id))
    ).scalar_one_or_none()
    await session.execute(delete(Assignment).where(Assignment.line_id.in_([line.id, following.id])))
    if tail_assignment is not None:
        session.add(Assignment(line_id=line.id, participant_id=tail_assignment.participant_id))

    await session.delete(following)
    await session.flush()
    await _shift_indexes(session, room_id, from_idx=following.idx + 1, delta=-1)
    await _recut_segment(room_id, line)
    await session.flush()
    await emit_event(
        session,
        room_id,
        "line.merged",
        {"line_id": str(line.id), "removed_line_id": str(following.id)},
    )
    return line


async def patch_speaker(
    session: AsyncSession,
    room_id: str,
    speaker_key: str,
    *,
    label: str | None = None,
    merge_into: str | None = None,
) -> dict[str, Any]:
    """Переименовать спикера или объединить его с другим — одной транзакцией."""
    if merge_into is None and not label:
        raise Conflict("Нужен label или merge_into", code="nothing_to_do")

    target_key = merge_into or speaker_key
    target_label = label
    if merge_into is not None and target_label is None:
        source_labels = (
            await session.execute(
                select(DialogueLine.speaker_label)
                .where(DialogueLine.room_id == room_id, DialogueLine.speaker_key == merge_into)
                .limit(1)
            )
        ).scalar_one_or_none()
        if source_labels is None:
            raise NotFound("Спикер для объединения не найден", code="speaker_not_found")
        target_label = source_labels

    values: dict[str, Any] = {"speaker_key": target_key}
    if target_label:
        values["speaker_label"] = target_label
    result = await session.execute(
        update(DialogueLine)
        .where(DialogueLine.room_id == room_id, DialogueLine.speaker_key == speaker_key)
        .values(**values, version=DialogueLine.version + 1, is_edited=True)
        .returning(DialogueLine.id)
    )
    affected = [row[0] for row in result.all()]
    if not affected:
        raise NotFound("Спикер не найден", code="speaker_not_found")
    await session.flush()
    await emit_event(
        session,
        room_id,
        "speaker.updated",
        {"speaker_key": speaker_key, "into": target_key, "lines": len(affected)},
    )
    return {"speaker_key": target_key, "label": target_label, "lines": len(affected)}


# ------------------------------------------------------------------ вспомогательное


def _slug(label: str) -> str:
    """Стабильный ключ спикера из подписи: «Woman 1» → «woman_1» (одна подпись = один ключ)."""
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in label.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")[:32] or "spk_0"


def _word_mid(word: dict) -> int:
    return int((word.get("t0", 0) + word.get("t1", 0)) / 2)


def _join_words(words: Sequence[dict]) -> str:
    return " ".join(str(word.get("w", "")).strip() for word in words).strip()


def _filter_words(words: list[dict] | None, start_ms: int, end_ms: int) -> list[dict] | None:
    if not words:
        return words
    kept = [word for word in words if start_ms <= _word_mid(word) <= end_ms]
    return kept or None


async def _shift_indexes(
    session: AsyncSession, room_id: str, *, from_idx: int, delta: int
) -> None:
    """Сдвинуть нумерацию реплик, не нарушая уникальный индекс `(room_id, idx)`.

    Сдвиг в один проход ломается на промежуточном состоянии (например, 5→6 при живой 6),
    поэтому сначала уводим диапазон в «карантин» (+100000), затем возвращаем со сдвигом.
    """
    if delta == 0:
        return
    quarantine = 100_000
    await session.execute(
        update(DialogueLine)
        .where(DialogueLine.room_id == room_id, DialogueLine.idx >= from_idx)
        .values(idx=DialogueLine.idx + quarantine)
    )
    await session.flush()
    await session.execute(
        update(DialogueLine)
        .where(DialogueLine.room_id == room_id, DialogueLine.idx >= from_idx + quarantine)
        .values(idx=DialogueLine.idx - quarantine + delta)
    )
    await session.flush()


async def _assert_no_overlap(
    session: AsyncSession, room_id: str, line: DialogueLine, start_ms: int, end_ms: int
) -> None:
    """Границы реплики не должны залезать на соседей: иначе таймлайн и субтитры разъедутся."""
    neighbour = (
        await session.execute(
            select(DialogueLine)
            .where(
                DialogueLine.room_id == room_id,
                DialogueLine.id != line.id,
                DialogueLine.start_ms < end_ms,
                DialogueLine.end_ms > start_ms,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if neighbour is not None:
        raise Conflict(
            "Границы пересекаются с соседней репликой",
            code="line_bounds_conflict",
            details={"conflict_line_id": str(neighbour.id), "idx": neighbour.idx},
        )


async def _has_overlap(session: AsyncSession, room_id: str, line: DialogueLine) -> bool:
    rows = (
        await session.execute(
            select(func.count())
            .select_from(DialogueLine)
            .where(
                DialogueLine.room_id == room_id,
                DialogueLine.id != line.id,
                DialogueLine.start_ms < line.end_ms,
                DialogueLine.end_ms > line.start_ms,
            )
        )
    ).scalar_one()
    return bool(rows)


async def bulk_update_speaker(
    session: AsyncSession, room_id: str, line_ids: Iterable[uuid.UUID], label: str
) -> int:
    """Проставить одного спикера нескольким репликам (используется UI правки диалога)."""
    ids = list(line_ids)
    if not ids:
        return 0
    result = await session.execute(
        update(DialogueLine)
        .where(DialogueLine.room_id == room_id, DialogueLine.id.in_(ids))
        .values(speaker_label=label, speaker_key=_slug(label), version=DialogueLine.version + 1)
        .returning(DialogueLine.id)
    )
    count = len(result.all())
    await emit_event(session, room_id, "speaker.bulk", {"lines": count, "label": label})
    return count
