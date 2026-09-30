"""Загрузка видео: потоковое сохранение, валидация, регистрация в БД."""

from __future__ import annotations

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

    storage.ensure_space(settings.max_upload_bytes)
    filename = "source" + _extension(upload.filename)
    rel_path, size_bytes, sha256 = await storage.save_upload_stream(
        upload,
        room.id,
        "original",
        filename=filename,
        max_bytes=settings.max_upload_bytes,
    )
    abs_path = storage.absolute(rel_path)

    try:
        info = probe.validate_upload(abs_path)
    except Exception:
        abs_path.unlink(missing_ok=True)
        raise

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
