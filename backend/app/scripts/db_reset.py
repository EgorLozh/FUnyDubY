"""Скрипты обслуживания (только dev)."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine

TABLES = [
    "room_events",
    "assignments",
    "recordings",
    "dialogue_lines",
    "job_stages",
    "processing_jobs",
    "render_jobs",
    "videos",
    "participants",
    "rooms",
]


async def main() -> int:
    if settings.app_env == "production":
        print("Отказ: db_reset в production запрещён")
        return 1
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))
    await engine.dispose()
    print(f"Очищено таблиц: {len(TABLES)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
