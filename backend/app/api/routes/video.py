"""Видео комнаты: загрузка, метаданные, стриминг с поддержкой Range."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import ParticipantDep, RoomDep, SessionDep
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.schemas.video import VideoOut
from app.services import storage, video as video_service

log = get_logger("video.api")
router = APIRouter(prefix="/api/rooms", tags=["video"])

CONTENT_TYPES = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "matroska": "video/x-matroska",
    "webm": "video/webm",
}


@router.post(
    "/{room_id}/video",
    response_model=VideoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_video(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    file: Annotated[UploadFile, File(description="MP4/WebM/MOV, до 10 минут")],
    replace: Annotated[bool, Query()] = False,
) -> VideoOut:
    video = await video_service.upload_video(session, room, file, replace=replace)
    return VideoOut.model_validate(video)


@router.get("/{room_id}/video", response_model=VideoOut)
async def get_video(room: RoomDep, session: SessionDep) -> VideoOut:
    video = await video_service.get_video_or_404(session, room)
    return VideoOut.model_validate(video)


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Разобрать `Range: bytes=start-end`. Возвращает (start, end) включительно."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":  # суффикс: последние N байт
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, size - length)
            return start, size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start >= size:
        return None
    return start, min(end, size - 1)


@router.get("/{room_id}/video/file")
async def stream_video(request: Request, room: RoomDep, session: SessionDep):
    """Отдать исходный файл. Всегда через API — том наружу не выставлен."""
    video = await video_service.get_video_or_404(session, room)
    path = storage.absolute(video.source_path)
    if not path.exists():
        raise NotFound("Файл видео не найден в хранилище", code="video_file_missing")

    size = path.stat().st_size
    content_type = CONTENT_TYPES.get(video.container.split(",")[0].strip(), "video/mp4")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{video.original_filename}"',
        "Cache-Control": "private, max-age=3600",
    }

    rng = _parse_range(request.headers.get("Range"), size)
    if rng is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(
            storage.stream_file(path), media_type=content_type, headers=headers
        )

    start, end = rng
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        storage.stream_file(path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers=headers,
    )


@router.get("/{room_id}/video/thumb")
async def video_thumb(room: RoomDep, session: SessionDep, at_ms: Annotated[int, Query(ge=0)] = 0):
    """Кадр-превью (используется в UI для полосы реплик)."""
    import subprocess
    import tempfile

    from app.core.config import settings

    video = await video_service.get_video_or_404(session, room)
    source = storage.absolute(video.source_path)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "thumb.jpg"
        result = subprocess.run(  # noqa: S603
            [
                settings.ffmpeg_bin,
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-ss",
                f"{at_ms / 1000:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-f",
                "image2",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not out.exists():
            log.warning("thumb_failed", stderr=result.stderr[-300:])
            raise NotFound("Не удалось получить кадр", code="thumb_failed")
        data = out.read_bytes()
    return StreamingResponse(iter([data]), media_type="image/jpeg")
