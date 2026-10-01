"""Тейки озвучки: чтение того, что уже записано.

Этап 9 добавит сюда загрузку тейков; сейчас модуль нужен списку реплик (флаг «озвучена»)
и отдаче актуальной записи через `/media/recording/{line_id}`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.models import DialogueLine, Recording


async def current_recordings(
    session: AsyncSession, room_id: str
) -> dict[uuid.UUID, Recording]:
    """Актуальные тейки всех реплик комнаты одним запросом."""
    rows = (
        (
            await session.execute(
                select(Recording)
                .join(DialogueLine, DialogueLine.id == Recording.line_id)
                .where(DialogueLine.room_id == room_id, Recording.is_current.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return {row.line_id: row for row in rows}


async def list_takes(
    session: AsyncSession, room_id: str, line_id: uuid.UUID
) -> list[Recording]:
    rows = (
        (
            await session.execute(
                select(Recording)
                .join(DialogueLine, DialogueLine.id == Recording.line_id)
                .where(DialogueLine.room_id == room_id, Recording.line_id == line_id)
                .order_by(Recording.take_number)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_current(session: AsyncSession, line_id: uuid.UUID) -> Recording | None:
    return (
        await session.execute(
            select(Recording).where(Recording.line_id == line_id, Recording.is_current.is_(True))
        )
    ).scalar_one_or_none()


async def get_recording(
    session: AsyncSession, room_id: str, line_id: uuid.UUID, recording_id: uuid.UUID
) -> Recording:
    recording = (
        await session.execute(
            select(Recording)
            .join(DialogueLine, DialogueLine.id == Recording.line_id)
            .where(
                Recording.id == recording_id,
                Recording.line_id == line_id,
                DialogueLine.room_id == room_id,
            )
        )
    ).scalar_one_or_none()
    if recording is None:
        raise NotFound("Запись не найдена", code="recording_not_found")
    return recording
