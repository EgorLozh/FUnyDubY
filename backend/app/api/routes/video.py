"""Видео комнаты: загрузка, метаданные, стриминг с поддержкой Range."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import ParticipantDep, RoomDep, SessionDep
from app.api.files import file_response
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.schemas.video import VideoOut
from app.services import jobs as jobs_service
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
    autostart: Annotated[bool, Query(description="сразу запустить обработку")] = True,
) -> VideoOut:
    video = await video_service.upload_video(session, room, file, replace=replace)
    if autostart:
        # Обработка стартует сама после загрузки (§6 архитектуры): этап extract_audio идёт первым
        job = await jobs_service.create_or_reset_job(session, room, video, scope="all")
        await jobs_service.enqueue_first_stage(job.id)
    return VideoOut.model_validate(video)


@router.get("/{room_id}/video", response_model=VideoOut)
async def get_video(room: RoomDep, session: SessionDep) -> VideoOut:
    video = await video_service.get_video_or_404(session, room)
    return VideoOut.model_validate(video)


@router.get("/{room_id}/video/file")
async def stream_video(request: Request, room: RoomDep, session: SessionDep):
    """Отдать исходный файл. Всегда через API — том наружу не выставлен."""
    video = await video_service.get_video_or_404(session, room)
    path = storage.absolute(video.source_path)
    if not path.exists():
        raise NotFound("Файл видео не найден в хранилище", code="video_file_missing")

    content_type = CONTENT_TYPES.get(video.container.split(",")[0].strip(), "video/mp4")
    return file_response(
        request,
        path,
        content_type=content_type,
        filename=video.original_filename,
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
