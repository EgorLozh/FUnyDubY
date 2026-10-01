"""Сборка финальной озвучки: заявка на рендер, статусы, история, отмена.

Идемпотентность сделана на уровне БД (уникальный индекс `room_id + options_hash + lines_version`),
поэтому «версия диалога» — не счётчик правок, а отпечаток содержимого: она меняется и от правки
текста реплики, и от смены актуального тейка. Одинаковое содержимое + одинаковые параметры = та же
сборка: повторная заявка не запускает работу заново, а отдаёт готовый или идущий результат.
Упавшую или отменённую сборку тот же запрос перезапускает.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.models import DialogueLine, Recording, RenderJob, RenderStatus, Room, Video

log = get_logger("renders")

ACTIVE_STATUSES = (RenderStatus.QUEUED, RenderStatus.MIXING, RenderStatus.ENCODING)


def _options_hash(options: dict[str, Any], lines_version: int) -> str:
    payload = json.dumps(options, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{payload}|v{lines_version}".encode()).hexdigest()


async def room_lines_version(session: AsyncSession, room_id: str) -> int:
    """Отпечаток диалога комнаты: реплики, их версии и актуальные тейки.

    Считается по содержимому, а не по количеству правок: смена актуального тейка не меняет
    версию строки реплики, но обязана инвалидировать готовую сборку.
    """
    rows = await session.execute(
        select(DialogueLine.id, DialogueLine.version, Recording.id)
        .outerjoin(
            Recording,
            (Recording.line_id == DialogueLine.id)
            & Recording.is_current.is_(True)
            & Recording.orphaned.is_(False),
        )
        .where(DialogueLine.room_id == room_id)
    )
    parts = sorted(f"{row[0]}:{row[1]}:{row[2] or '-'}" for row in rows)
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) % 2147483647


def _recorded_lines_query(room_id: str) -> Select:
    return (
        select(func.count(func.distinct(Recording.line_id)))
        .select_from(Recording)
        .join(DialogueLine, DialogueLine.id == Recording.line_id)
        .where(
            DialogueLine.room_id == room_id,
            Recording.is_current.is_(True),
            Recording.orphaned.is_(False),
        )
    )


async def create_render(
    session: AsyncSession, room: Room, participant_id: uuid.UUID | None, options: dict | None = None
) -> tuple[RenderJob, bool]:
    """Создать заявку на рендер. Возвращает (рендер, работа_уже_была_начата)."""
    options = {**(options or {})}
    options.setdefault("unrecorded", settings.unrecorded_policy)
    options.setdefault("loudness_lufs", settings.render_loudness_target)
    if options.get("unrecorded") not in ("silent", "original"):
        raise Conflict("unrecorded должен быть silent или original", code="bad_options")

    video = await session.get(Video, room.video_id) if room.video_id else None
    if video is None:
        raise Conflict("В комнате нет исходного видео", code="no_video")

    total_lines = int(
        await session.scalar(
            select(func.count()).select_from(DialogueLine).where(DialogueLine.room_id == room.id)
        )
        or 0
    )
    if total_lines == 0:
        raise Conflict("В комнате нет реплик", code="no_lines")

    recorded_lines = int(await session.scalar(_recorded_lines_query(room.id)) or 0)
    if recorded_lines == 0:
        raise Conflict("Ни одна реплика не озвучена", code="nothing_recorded")

    lines_version = await room_lines_version(session, room.id)
    options_hash = _options_hash(options, lines_version)

    existing = await session.scalar(
        select(RenderJob).where(
            RenderJob.room_id == room.id, RenderJob.options_hash == options_hash
        )
    )
    if existing is not None:
        if existing.status in ACTIVE_STATUSES or existing.status == RenderStatus.DONE:
            # Та же сборка уже идёт или готова — второй раз не считаем
            return existing, True
        # Упавшую/отменённую переиспользуем тем же рядом: перезапуск без дубликатов
        existing.status = RenderStatus.QUEUED
        existing.progress = 0
        existing.error = None
        existing.started_at = None
        existing.finished_at = None
        existing.total_lines = total_lines
        existing.recorded_lines = recorded_lines
        await session.flush()
        log.info("render_restarted", room_id=room.id, render_id=str(existing.id))
        return existing, False

    render = RenderJob(
        room_id=room.id,
        status=RenderStatus.QUEUED,
        options=options,
        options_hash=options_hash,
        lines_version=lines_version,
        total_lines=total_lines,
        recorded_lines=recorded_lines,
        progress=0,
    )
    session.add(render)
    await session.flush()
    log.info(
        "render_created",
        room_id=room.id,
        render_id=str(render.id),
        total_lines=total_lines,
        recorded_lines=recorded_lines,
        participant_id=str(participant_id) if participant_id else None,
    )
    return render, False


async def get_render(session: AsyncSession, room_id: str, render_id: uuid.UUID) -> RenderJob:
    render = await session.scalar(
        select(RenderJob).where(RenderJob.id == render_id, RenderJob.room_id == room_id)
    )
    if render is None:
        raise NotFound("Рендер не найден", code="render_not_found")
    return render


async def list_renders(session: AsyncSession, room_id: str, limit: int = 20) -> list[RenderJob]:
    rows = await session.scalars(
        select(RenderJob)
        .where(RenderJob.room_id == room_id)
        .order_by(RenderJob.created_at.desc())
        .limit(limit)
    )
    return list(rows)


async def current_render(session: AsyncSession, room_id: str) -> RenderJob | None:
    return await session.scalar(
        select(RenderJob).where(RenderJob.room_id == room_id, RenderJob.is_current.is_(True))
    )


async def cancel_render(session: AsyncSession, room: Room, render_id: uuid.UUID) -> RenderJob:
    render = await get_render(session, room.id, render_id)
    if render.status not in ACTIVE_STATUSES:
        raise Conflict(f"Рендер уже в состоянии {render.status.value}", code="render_not_active")
    render.status = RenderStatus.CANCELED
    render.error = {"code": "canceled", "message": "Отменено пользователем"}
    await session.flush()
    log.info("render_canceled", room_id=room.id, render_id=str(render.id))
    return render


async def mark_current(session: AsyncSession, render: RenderJob) -> None:
    """Сделать рендер актуальным: у комнаты актуальным может быть только один результат."""
    await session.execute(
        update(RenderJob)
        .where(RenderJob.room_id == render.room_id, RenderJob.id != render.id)
        .values(is_current=False)
    )
    render.is_current = True
    await session.flush()
