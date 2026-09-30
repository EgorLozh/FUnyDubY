"""Зависимости FastAPI: сессия, комната, текущий участник."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Path
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import DomainError, Forbidden, NotFound, Unauthorized
from app.core.logging import get_logger
from app.core.security import token_matches
from app.models import Participant, Room

log = get_logger("deps")


class RoomGone(DomainError):
    status_code = 410
    code = "room_deleted"


async def get_room(
    room_id: Annotated[str, Path(min_length=20, max_length=20)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Room:
    room = (await session.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None:
        # Единый ответ без деталей — не подтверждаем существование комнаты
        raise NotFound("Комната не найдена", code="room_not_found")
    if room.deleted_at is not None:
        raise RoomGone("Комната удалена")
    return room


async def get_current_participant(
    room: Annotated[Room, Depends(get_room)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_participant_token: Annotated[str | None, Header(alias="X-Participant-Token")] = None,
) -> Participant:
    if not x_participant_token:
        raise Unauthorized("Нужен заголовок X-Participant-Token", code="participant_token_required")
    participants = (
        (await session.execute(select(Participant).where(Participant.room_id == room.id)))
        .scalars()
        .all()
    )
    for participant in participants:
        if token_matches(x_participant_token, participant.token_hash):
            participant.last_seen_at = datetime.now(UTC)
            return participant
    raise Unauthorized("Неизвестный участник комнаты", code="participant_token_invalid")


async def get_optional_participant(
    room: Annotated[Room, Depends(get_room)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_participant_token: Annotated[str | None, Header(alias="X-Participant-Token")] = None,
) -> Participant | None:
    if not x_participant_token:
        return None
    try:
        return await get_current_participant(room, session, x_participant_token)
    except Unauthorized:
        return None


def require_creator(room: Room, participant: Participant) -> None:
    if room.owner_participant_id and room.owner_participant_id != participant.id:
        raise Forbidden("Действие доступно только создателю комнаты", code="creator_only")


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RoomDep = Annotated[Room, Depends(get_room)]
ParticipantDep = Annotated[Participant, Depends(get_current_participant)]
OptionalParticipantDep = Annotated[Participant | None, Depends(get_optional_participant)]


def problem_response(error: DomainError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.to_problem())
