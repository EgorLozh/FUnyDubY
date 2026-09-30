"""Комнаты: создание, чтение, настройки, удаление."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict
from app.core.logging import get_logger
from app.core.security import generate_room_id, room_dir
from app.models import (
    Assignment,
    DialogueLine,
    Participant,
    ProcessingJob,
    Recording,
    Room,
    RoomEvent,
    RoomStatus,
    Video,
)
from app.services import storage

log = get_logger("rooms")


def default_settings() -> dict[str, Any]:
    """Настройки комнаты по умолчанию — берутся из окружения, а не из кода."""
    return {
        "stt_backend": settings.stt_backend,
        "stt_language": settings.stt_language,
        "stt_prompt": "",
        "max_speakers": settings.max_speakers_default,
        "ducking_db": settings.default_ducking_db,
        "unrecorded_policy": settings.unrecorded_policy,
        "separation_mode": settings.separation_mode,
        "merge_gap_ms": settings.merge_gap_ms,
        "max_line_ms": settings.max_line_ms,
        "record_tolerance_ms": settings.record_tolerance_ms,
    }


async def create_room(session: AsyncSession, title: str) -> Room:
    """Создать комнату. ID — криптостойкий; коллизия практически невозможна, но проверяем."""
    for _ in range(5):
        room_id = generate_room_id()
        room = Room(
            id=room_id,
            title=title.strip() or "Без названия",
            status=RoomStatus.CREATED,
            settings=default_settings(),
            expires_at=datetime.now(UTC) + timedelta(days=settings.room_ttl_days),
        )
        session.add(room)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            continue
        storage.ensure_room_layout(room_id)
        await session.commit()
        log.info("room_created", room_id=room_id)
        return room
    raise Conflict("Не удалось выделить идентификатор комнаты, попробуйте ещё раз")


async def get_counters(session: AsyncSession, room_id: str) -> dict[str, int]:
    lines = (
        await session.execute(
            select(func.count()).select_from(DialogueLine).where(DialogueLine.room_id == room_id)
        )
    ).scalar_one()
    participants = (
        await session.execute(
            select(func.count()).select_from(Participant).where(Participant.room_id == room_id)
        )
    ).scalar_one()
    recorded = (
        await session.execute(
            select(func.count(func.distinct(Recording.line_id)))
            .select_from(Recording)
            .join(DialogueLine, DialogueLine.id == Recording.line_id)
            .where(DialogueLine.room_id == room_id, Recording.is_current.is_(True))
        )
    ).scalar_one()
    assigned = (
        await session.execute(
            select(func.count())
            .select_from(Assignment)
            .join(DialogueLine, DialogueLine.id == Assignment.line_id)
            .where(DialogueLine.room_id == room_id)
        )
    ).scalar_one()
    return {
        "participants": participants,
        "lines": lines,
        "recorded_lines": recorded,
        "assigned_lines": assigned,
    }


async def get_current_video(session: AsyncSession, room: Room) -> Video | None:
    if room.video_id is None:
        return None
    return (await session.execute(select(Video).where(Video.id == room.video_id))).scalar_one_or_none()


async def get_latest_job(session: AsyncSession, room_id: str) -> ProcessingJob | None:
    return (
        await session.execute(
            select(ProcessingJob)
            .where(ProcessingJob.room_id == room_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def update_settings(session: AsyncSession, room: Room, patch: dict[str, Any]) -> Room:
    merged = {**default_settings(), **(room.settings or {})}
    merged.update({k: v for k, v in patch.items() if v is not None})
    room.settings = merged
    await session.commit()
    return room


async def soft_delete(session: AsyncSession, room: Room) -> None:
    """Мягкое удаление: комната сразу недоступна, файлы уносит purge_room."""
    room.deleted_at = datetime.now(UTC)
    room.status = RoomStatus.DELETING
    await session.commit()

    try:
        from app.workers.tasks.purge_room import purge_room

        purge_room.send(str(room.id))
    except Exception as exc:  # noqa: BLE001
        # Комната уже помечена удалённой; если очередь недоступна, её подберёт
        # expire_rooms/reconcile — терять из-за этого ответ пользователю нельзя.
        log.error("purge_enqueue_failed", room_id=room.id, error=str(exc))
    log.info("room_soft_deleted", room_id=room.id)


async def emit_event(
    session: AsyncSession, room_id: str, type_: str, payload: dict[str, Any] | None = None
) -> None:
    """Записать событие в журнал и опубликовать в Redis (для SSE)."""
    event = RoomEvent(room_id=room_id, type=type_, payload=payload or {})
    session.add(event)
    await session.commit()

    from app.core.redis import get_redis, room_channel

    try:
        await get_redis().publish(
            room_channel(room_id),
            json.dumps({"id": event.id, "type": type_, "payload": payload or {}}),
        )
    except Exception as exc:  # noqa: BLE001 — событие не должно ломать запрос
        log.warning("event_publish_failed", room_id=room_id, type=type_, error=str(exc))


def room_dir_path(room_id: str):
    return room_dir(room_id)
