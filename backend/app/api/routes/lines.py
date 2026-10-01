"""Реплики диалога: чтение, список спикеров, аудио оригинала реплики."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Path as PathParam, Query
from fastapi.responses import StreamingResponse

from app.api.deps import RoomDep, SessionDep
from app.core.errors import NotFound
from app.schemas.line import LineOut, SpeakerOut, line_to_out
from app.services import lines as lines_service
from app.services import storage

router = APIRouter(prefix="/api/rooms", tags=["lines"])


@router.get("/{room_id}/lines", response_model=list[LineOut])
async def list_lines(
    room: RoomDep,
    session: SessionDep,
    speaker: Annotated[str | None, Query(description="speaker_key или подпись спикера")] = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LineOut]:
    rows = await lines_service.list_lines(
        session, room.id, speaker=speaker, limit=limit, offset=offset
    )
    return [line_to_out(row) for row in rows]


@router.get("/{room_id}/lines/{line_id}", response_model=LineOut)
async def get_line(
    room: RoomDep,
    session: SessionDep,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> LineOut:
    line = await lines_service.get_line(session, room.id, line_id)
    return line_to_out(line)


@router.get("/{room_id}/speakers", response_model=list[SpeakerOut])
async def list_speakers(room: RoomDep, session: SessionDep) -> list[SpeakerOut]:
    return [SpeakerOut(**item) for item in await lines_service.list_speakers(session, room.id)]


@router.get("/{room_id}/lines/{line_id}/original")
async def original_audio(
    room: RoomDep,
    session: SessionDep,
    line_id: Annotated[uuid.UUID, PathParam()],
):
    """Фрагмент оригинальной речи реплики — то, что участник слушает перед записью."""
    line = await lines_service.get_line(session, room.id, line_id)
    if not line.speech_path:
        raise NotFound("Для реплики нет фрагмента оригинальной речи", code="no_original_audio")
    path: Path = storage.absolute(line.speech_path)
    if not path.exists():
        raise NotFound("Файл фрагмента не найден", code="original_audio_missing")
    size = path.stat().st_size
    return StreamingResponse(
        storage.stream_file(path),
        media_type="audio/wav",
        headers={"Content-Length": str(size), "Accept-Ranges": "bytes"},
    )
