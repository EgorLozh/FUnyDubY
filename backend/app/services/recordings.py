"""Тейки озвучки: загрузка, актуализация, удаление, чтение.

Инварианты, которые держит этот модуль:

* **один актуальный тейк на реплику** — частичный уникальный индекс `is_current` в БД;
  при смене актуального тейка сначала снимаем флаг у прежнего, потом ставим новому;
* **тейк принадлежит реплике комнаты** — все запросы идут с проверкой `room_id`;
* **нумерация тейков непрерывная** — `take_number` = максимум по реплике + 1.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.models import DialogueLine, Recording, RecordingStatus


async def list_takes(session: AsyncSession, room_id: str, line_id: uuid.UUID) -> list[Recording]:
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


async def current_recordings(session: AsyncSession, room_id: str) -> dict[uuid.UUID, Recording]:
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


async def next_take_number(session: AsyncSession, line_id: uuid.UUID) -> int:
    value = (
        await session.execute(
            select(func.coalesce(func.max(Recording.take_number), 0)).where(
                Recording.line_id == line_id
            )
        )
    ).scalar_one()
    return int(value) + 1


async def _clear_current(session: AsyncSession, line_id: uuid.UUID) -> None:
    await session.execute(
        update(Recording).where(Recording.line_id == line_id, Recording.is_current.is_(True)).values(
            is_current=False
        )
    )


async def create_take(
    session: AsyncSession,
    *,
    line_id: uuid.UUID,
    participant_id: uuid.UUID | None,
    raw_path: str,
    processed_path: str | None,
    duration_ms: int,
    size_bytes: int,
    sample_rate: int | None,
    channels: int | None,
    loudness: float | None,
    line_version: int | None,
    make_current: bool = True,
) -> Recording:
    take_number = await next_take_number(session, line_id)
    if make_current:
        await _clear_current(session, line_id)
    recording = Recording(
        line_id=line_id,
        participant_id=participant_id,
        take_number=take_number,
        status=RecordingStatus.READY if processed_path else RecordingStatus.UPLOADED,
        is_current=make_current,
        raw_path=raw_path,
        processed_path=processed_path,
        duration_ms=duration_ms,
        sample_rate=sample_rate,
        channels=channels,
        loudness_lufs=loudness,
        size_bytes=size_bytes,
        line_version_at_record=line_version,
    )
    session.add(recording)
    await session.flush()
    return recording


async def set_current(
    session: AsyncSession, room_id: str, line_id: uuid.UUID, recording_id: uuid.UUID
) -> Recording:
    recording = await get_recording(session, room_id, line_id, recording_id)
    await _clear_current(session, line_id)
    recording.is_current = True
    await session.flush()
    return recording


def takes_dir(room_id: str, line_id: uuid.UUID) -> tuple[str, Path]:
    """Куда складывать файлы тейков: (относительный каталог, абсолютный путь)."""
    from app.core.security import room_relative
    from app.services import storage

    relative = room_relative(room_id, "recordings", str(line_id))
    return relative, storage.absolute(relative)
