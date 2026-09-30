"""Участники комнаты: регистрация без аккаунта, токен, имена."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.core.security import generate_participant_token, hash_token
from app.models import Participant, Room

log = get_logger("participants")

PALETTE = [
    "#7c9cff",
    "#ff9f7c",
    "#8ce99a",
    "#ffd43b",
    "#d0a2ff",
    "#63e6be",
    "#ffa8c1",
    "#a5d8ff",
]


async def _next_color(session: AsyncSession, room_id: str) -> str:
    count = (
        await session.execute(
            select(func.count()).select_from(Participant).where(Participant.room_id == room_id)
        )
    ).scalar_one()
    return PALETTE[count % len(PALETTE)]


async def register_participant(
    session: AsyncSession, room: Room, display_name: str
) -> tuple[Participant, str]:
    """Создать участника и вернуть его токен (единственный раз, когда токен виден)."""
    name = display_name.strip()
    if not name:
        raise Conflict("Имя не может быть пустым", code="empty_name")

    token = generate_participant_token()
    participant = Participant(
        room_id=room.id,
        display_name=name,
        color=await _next_color(session, room.id),
        token_hash=hash_token(token),
        is_creator=room.owner_participant_id is None,
        last_seen_at=datetime.now(UTC),
    )
    session.add(participant)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            f"Имя «{name}» уже занято в этой комнате — выберите другое",
            code="name_taken",
        ) from exc

    if room.owner_participant_id is None:
        room.owner_participant_id = participant.id
    await session.commit()
    log.info("participant_registered", room_id=room.id, participant_id=str(participant.id))
    return participant, token


async def list_participants(session: AsyncSession, room_id: str) -> list[Participant]:
    return list(
        (
            await session.execute(
                select(Participant)
                .where(Participant.room_id == room_id)
                .order_by(Participant.created_at)
            )
        )
        .scalars()
        .all()
    )


async def rename_participant(
    session: AsyncSession, participant: Participant, display_name: str, color: str | None
) -> Participant:
    name = display_name.strip()
    if name and name != participant.display_name:
        participant.display_name = name
    if color:
        participant.color = color
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(f"Имя «{name}» уже занято в этой комнате", code="name_taken") from exc
    return participant


async def get_participant_or_404(
    session: AsyncSession, room_id: str, participant_id
) -> Participant:
    participant = (
        await session.execute(
            select(Participant).where(
                Participant.id == participant_id, Participant.room_id == room_id
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise NotFound("Участник не найден", code="participant_not_found")
    return participant
