"""purge_room: удаление файлов и строки комнаты (идемпотентно)."""

from __future__ import annotations

import asyncio
import shutil

import dramatiq
from sqlalchemy import delete

from app.core.db import WorkerSessionFactory
from app.core.logging import get_logger
from app.models import Room, RoomStatus
from app.services import storage

log = get_logger("purge_room")


async def _purge(room_id: str) -> dict[str, object]:
    # 1. Файлы
    size_before = 0
    files_ok = True
    try:
        size_before = storage.room_size_bytes(room_id)
        storage.delete_room(room_id)
        # `delete_room` глотает ошибки rmtree — проверяем факт, а не отсутствие исключения.
        # Пустой скелет каталогов (файлов нет) потерей данных не считаем, но и мусор не оставляем.
        leftover = storage.room_size_bytes(room_id)
        if leftover > 0:
            raise RuntimeError(f"на диске осталось {leftover} байт")
        shutil.rmtree(storage.room_storage_dir(room_id), ignore_errors=True)
        if storage.room_storage_dir(room_id).exists():
            raise RuntimeError("каталог комнаты остался на диске")
    except Exception as exc:  # noqa: BLE001
        log.error("purge_files_failed", room_id=room_id, error=str(exc))
        size_before = -1
        files_ok = False

    # Файлы не убрались — строку комнаты НЕ удаляем: иначе видео и записи останутся на диске
    # без единой ссылки в БД, и найти их будет уже нечем. Комната остаётся в DELETING,
    # обслуживание повторит попытку.
    if not files_ok:
        log.warning("purge_deferred", room_id=room_id)
        return {"room_id": room_id, "deleted": False, "deferred": True}

    # 2. Строка комнаты (каскад уносит детей)
    async with WorkerSessionFactory() as session:
        row = await session.get(Room, room_id)
        if row is None:
            log.info("purge_already_gone", room_id=room_id)
            return {"room_id": room_id, "deleted": False}
        await session.execute(delete(Room).where(Room.id == room_id))
        await session.commit()

    log.info("room_purged", room_id=room_id, bytes_freed=size_before)
    return {"room_id": room_id, "deleted": True, "bytes_freed": size_before}


@dramatiq.actor(queue_name="system", max_retries=3, time_limit=600_000)
def purge_room(room_id: str) -> None:
    result = asyncio.run(_purge(room_id))
    log.info("purge_done", **result)


@dramatiq.actor(queue_name="system", max_retries=1, time_limit=600_000)
def expire_rooms() -> None:
    """Пометить просроченные комнаты на удаление и отправить purge."""

    async def _run() -> list[str]:
        from datetime import UTC, datetime

        from sqlalchemy import select

        async with WorkerSessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Room).where(
                            Room.deleted_at.is_(None),
                            Room.expires_at.is_not(None),
                            Room.expires_at < datetime.now(UTC),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for room in rows:
                room.status = RoomStatus.DELETING
            await session.commit()
            return [room.id for room in rows]

    room_ids = asyncio.run(_run())
    for room_id in room_ids:
        purge_room.send(room_id)
    log.info("expire_rooms_scan", candidates=len(room_ids))
