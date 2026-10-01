"""Единая отдача медиа комнаты: речь, фон, оригинал реплики, актуальная запись.

Один маршрут вместо четырёх почти одинаковых: `kind` говорит, что именно нужно, `ref` —
к чему (идентификатор реплики или записи). Это упрощает клиент (один способ получить аудио)
и оставляет одну точку для проверки принадлежности объекта комнате.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Path as PathParam, Request

from app.core.errors import NotFound
from app.core.security import room_relative
from app.api.deps import RoomDep, SessionDep
from app.api.files import file_response
from app.services import lines as lines_service
from app.services import recordings as recordings_service
from app.services import storage

router = APIRouter(prefix="/api/rooms", tags=["media"])

MediaKind = Literal["speech", "background", "original", "recording"]

CONTENT_TYPES = {
    "speech": "audio/wav",
    "background": "audio/wav",
    "original": "audio/wav",
    "recording": "audio/webm",
}


async def _resolve(
    kind: MediaKind, room_id: str, ref: str, session
) -> tuple[Path, str, str]:
    """Вернуть (путь, тип содержимого, имя файла) для запрошенного медиа."""
    if kind == "speech":
        relative = room_relative(room_id, "speech", "speech.wav")
        return storage.absolute(relative), "audio/wav", "speech.wav"
    if kind == "background":
        relative = room_relative(room_id, "speech", "background.wav")
        return storage.absolute(relative), "audio/wav", "background.wav"

    try:
        line_id = uuid.UUID(ref)
    except ValueError as exc:
        raise NotFound("Ожидался идентификатор реплики", code="bad_ref") from exc

    line = await lines_service.get_line(session, room_id, line_id)

    if kind == "original":
        if not line.speech_path:
            raise NotFound("Для реплики нет фрагмента речи", code="no_original_audio")
        return storage.absolute(line.speech_path), "audio/wav", f"line-{line.idx}-original.wav"

    recording = await recordings_service.get_current(session, line_id)
    if recording is None:
        raise NotFound("У реплики ещё нет записи", code="no_recording")
    relative = recording.processed_path or recording.raw_path
    return storage.absolute(relative), "audio/webm", f"line-{line.idx}-take.wav"


@router.get("/{room_id}/media/{kind}/{ref}")
async def media(
    request: Request,
    room: RoomDep,
    session: SessionDep,
    kind: MediaKind,
    ref: Annotated[str, PathParam(min_length=1, max_length=64)],
):
    path, content_type, filename = await _resolve(kind, room.id, ref, session)
    if not path.exists():
        raise NotFound("Файл не найден в хранилище", code="media_missing")
    return file_response(request, path, content_type=content_type, filename=filename)
