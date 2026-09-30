"""SSE-поток событий комнаты: прогресс обработки, изменения реплик и назначений.

Заменяет поллинг: клиент держит одно соединение, сервер пушит события из Redis Pub/Sub.
Если соединение с Redis потеряно — поток отдаёт keep-alive, клиент переподключается.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import RoomDep, SessionDep
from app.core.logging import get_logger
from app.core.redis import get_redis, room_channel

log = get_logger("events")
router = APIRouter(prefix="/api/rooms", tags=["events"])

KEEPALIVE_S = 15


async def _event_stream(request: Request, room_id: str) -> AsyncIterator[bytes]:
    client = get_redis()
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    try:
        await pubsub.subscribe(room_channel(room_id))
        yield b'event: hello\ndata: {"type":"hello"}\n\n'
        while True:
            if await request.is_disconnected():
                break
            try:
                message = await pubsub.get_message(timeout=KEEPALIVE_S)
            except Exception as exc:  # noqa: BLE001 — реконнект делает клиент
                log.warning("pubsub_error", room_id=room_id, error=str(exc))
                await asyncio.sleep(1)
                message = None
            if message and message.get("data"):
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield f"data: {data}\n\n".encode()
            else:
                yield b": keep-alive\n\n"
    finally:
        try:
            await pubsub.unsubscribe(room_channel(room_id))
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.get("/{room_id}/events")
async def room_events(request: Request, room: RoomDep, session: SessionDep):
    return StreamingResponse(
        _event_stream(request, room.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # отключает буферизацию nginx
        },
    )
