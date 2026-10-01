"""Чтение реплик диалога (расширяется на этапе 7: правки, склейка, спикеры)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.models import DialogueLine


async def list_lines(
    session: AsyncSession,
    room_id: str,
    *,
    speaker: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[DialogueLine]:
    query = select(DialogueLine).where(DialogueLine.room_id == room_id)
    if speaker:
        query = query.where(
            (DialogueLine.speaker_key == speaker) | (DialogueLine.speaker_label == speaker)
        )
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
    lines = await list_lines(session, room_id)
    summary: dict[str, dict] = {}
    for line in lines:
        item = summary.setdefault(
            line.speaker_key,
            {
                "speaker_key": line.speaker_key,
                "speaker_label": line.speaker_label,
                "lines": 0,
                "total_ms": 0,
            },
        )
        item["lines"] += 1
        item["total_ms"] += line.end_ms - line.start_ms
    return sorted(summary.values(), key=lambda item: item["speaker_label"])
