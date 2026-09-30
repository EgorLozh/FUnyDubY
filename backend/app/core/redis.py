"""Redis: брокер Dramatiq, локи, pub/sub событий комнаты, heartbeat воркеров."""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import settings

_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def room_channel(room_id: str) -> str:
    return f"room:{room_id}:events"


def room_lock_key(room_id: str, what: str) -> str:
    return f"lock:room:{room_id}:{what}"


async def check_redis() -> tuple[bool, str]:
    try:
        client = get_redis()
        pong = await client.ping()
        return bool(pong), "PONG"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
