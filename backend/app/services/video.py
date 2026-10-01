"""Загрузка видео: потоковое сохранение, валидация, регистрация в БД."""

from __future__ import annotations

import os
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.core.security import room_relative
from app.models import Room, RoomStatus, Video
from app.services import storage
from app.services.rooms import emit_event
from app.video import probe

log = get_logger("video")


async def get_video_or_404(session: AsyncSession, room: Room) -> Video:
    if room.video_id is None:
        raise NotFound("В комнате ещё нет видео", code="video_not_uploaded")
    video = (await session.execute(select(Video).where(Video.id == room.video_id))).scalar_one_or_none()
    if video is None:
        raise NotFound("В комнате ещё нет видео", code="video_not_uploaded")
    return video


async def upload_video(
    session: AsyncSession,
    room: Room,
    upload: UploadFile,
    *,
    replace: bool = False,
) -> Video:
    """Сохранить и провалидировать видео.

    Порядок важен: сначала пишем поток на диск (не в память!), затем валидируем,
    и только потом создаём строку в БД. Невалидный файл удаляется.
    """
    if room.video_id is not None and not replace:
        raise Conflict(
            "В комнате уже есть видео. Перезагрузка возможна только с replace=1",
            code="video_exists",
        )

    # Место проверяется по ходу потоковой записи (см. save_upload_stream): резервировать
    # max_upload целиком на почти полном диске нельзя — падали бы даже мелкие загрузки.
    storage.ensure_space()
    ext = _extension(upload.filename)

    # Пишем во временный файл с уникальным именем: валидация не должна затирать
    # уже загруженное видео, если новая попытка окажется невалидной.
    staged_rel, size_bytes, sha256 = await storage.save_upload_stream(
        upload,
        room.id,
        "tmp",
        filename=f"upload-{uuid4().hex}{ext}",
        max_bytes=settings.max_upload_bytes,
    )
    staged = storage.absolute(staged_rel)

    try:
        info = probe.validate_upload(staged)
    except Exception:
        staged.unlink(missing_ok=True)
        raise

    # Валидный файл занимает своё место атомарно
    final_rel = room_relative(room.id, "original", f"source{ext}")
    final_path = storage.absolute(final_rel)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, final_path)
    rel_path = final_rel

    video = Video(
        room_id=room.id,
        storage_dir=room_relative(room.id, "original"),
        source_path=rel_path,
        original_filename=_safe_display_name(upload.filename),
        container=info.container,
        video_codec=info.video_codec,
        audio_codec=info.audio_codec,
        size_bytes=size_bytes,
        duration_ms=info.duration_ms,
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        sha256=sha256,
        probe=info.probe,
    )
    session.add(video)
    await session.flush()

    # Старое видео (при replace) больше не нужно
    previous = room.video_id
    room.video_id = video.id
    room.status = RoomStatus.UPLOADED
    await session.commit()

    if previous and previous != video.id:
        from sqlalchemy import delete

        await session.execute(delete(Video).where(Video.id == previous))
        # Замена монтажа обнуляет всё, что из него следовало: реплики, тейки, назначения и сборки
        # относились к старому видео. Оставлять их — значит показывать «озвучку» поверх
        # картинки, которой больше нет.
        await _reset_derived(session, room.id)
        await session.commit()

    await emit_event(
        session,
        room.id,
        "video.uploaded",
        {
            "video_id": str(video.id),
            "duration_ms": video.duration_ms,
            "size_bytes": video.size_bytes,
        },
    )
    log.info(
        "video_uploaded",
        room_id=room.id,
        video_id=str(video.id),
        duration_ms=video.duration_ms,
        size_bytes=size_bytes,
        container=info.container,
    )
    return video


async def _reset_derived(session: AsyncSession, room_id: str) -> None:
    """Убрать всё, что было выведено из прежнего видео: реплики, тейки, назначения, сборки."""
    from sqlalchemy import delete

    from app.models import DialogueLine, RenderJob

    # Тейки и назначения уходят каскадом вместе с репликами (ondelete=CASCADE)
    await session.execute(delete(DialogueLine).where(DialogueLine.room_id == room_id))
    await session.execute(delete(RenderJob).where(RenderJob.room_id == room_id))
    await session.flush()
    storage.purge_derived(room_id)
    log.info("room_derived_reset", room_id=room_id, reason="video_replaced")


ALLOWED_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}


def _extension(filename: str | None) -> str:
    """Расширение только из белого списка — оно попадает в имя файла на диске."""
    if not filename:
        return ".mp4"
    import os

    ext = os.path.splitext(filename)[1].lower()
    return ext if ext in ALLOWED_EXTENSIONS else ".mp4"


def _safe_display_name(filename: str | None) -> str:
    """Имя файла для показа в UI: без путей и управляющих символов."""
    if not filename:
        return "video.mp4"
    name = filename.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in "\r\n\t")
    return (name or "video.mp4")[:200]
