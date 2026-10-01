"""Реплики диалога: чтение, правка, разделение/склейка, спикеры, аудио оригинала.

Все маршруты вложены в комнату (`/api/rooms/{room_id}/...`): так принадлежность реплики
комнате проверяется одним запросом, а не «доверяем идентификатору из тела» — это снимает
класс ошибок IDOR между комнатами.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Path as PathParam, Query, Request

from app.api.deps import OptionalParticipantDep, ParticipantDep, RoomDep, SessionDep
from app.api.files import file_response
from app.core.errors import NotFound
from app.schemas.line import (
    LineOut,
    LineSplitIn,
    LineUpdateIn,
    SpeakerOut,
    SpeakerPatchIn,
    line_to_out,
)
from app.services import assignments as assignments_service
from app.services import lines as lines_service
from app.services import recordings as recordings_service
from app.services import storage

router = APIRouter(prefix="/api/rooms", tags=["lines"])


async def _enrich(session, room_id: str, rows: list) -> list[LineOut]:
    """Дополнить реплики назначением и актуальной записью — без N+1 запросов."""
    assignments = await assignments_service.room_assignments(session, room_id)
    names = await assignments_service.participant_names(session, room_id)
    recordings = await recordings_service.current_recordings(session, room_id)
    result: list[LineOut] = []
    for row in rows:
        assignment = assignments.get(row.id)
        result.append(
            line_to_out(
                row,
                assigned_participant_id=assignment.participant_id if assignment else None,
                assigned_display_name=(
                    names.get(assignment.participant_id) if assignment else None
                ),
                assigned_expires_at=assignment.expires_at if assignment else None,
                current_recording=recordings.get(row.id),
            )
        )
    return result


@router.get("/{room_id}/lines", response_model=list[LineOut])
async def list_lines(
    room: RoomDep,
    session: SessionDep,
    participant: OptionalParticipantDep,
    speaker: Annotated[str | None, Query(description="speaker_key или подпись спикера")] = None,
    assigned_to: Annotated[
        str | None, Query(pattern="^(me|unassigned|all)$", description="фильтр по назначению")
    ] = None,
    since_version: Annotated[int | None, Query(ge=0, description="только изменённые позже")] = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LineOut]:
    rows = await lines_service.list_lines(
        session,
        room.id,
        speaker=speaker,
        assigned_to=assigned_to,
        participant_id=participant.id if participant else None,
        since_version=since_version,
        limit=limit,
        offset=offset,
    )
    return await _enrich(session, room.id, rows)


@router.get("/{room_id}/lines/{line_id}", response_model=LineOut)
async def get_line(
    room: RoomDep,
    session: SessionDep,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> LineOut:
    line = await lines_service.get_line(session, room.id, line_id)
    return (await _enrich(session, room.id, [line]))[0]


@router.patch("/{room_id}/lines/{line_id}", response_model=LineOut)
async def patch_line(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    payload: LineUpdateIn,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> LineOut:
    """Правка реплики: текст, спикер, границы. `expected_version` — защита от перезаписи."""
    line = await lines_service.update_line(
        session,
        room.id,
        line_id,
        text=payload.text,
        speaker_label=payload.speaker_label,
        start_ms=payload.start_ms,
        end_ms=payload.end_ms,
        expected_version=payload.expected_version,
    )
    return (await _enrich(session, room.id, [line]))[0]


@router.post("/{room_id}/lines/{line_id}/split", response_model=list[LineOut])
async def split_line(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    payload: LineSplitIn,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> list[LineOut]:
    head, tail = await lines_service.split_line(session, room.id, line_id, at_ms=payload.at_ms)
    return await _enrich(session, room.id, [head, tail])


@router.post("/{room_id}/lines/{line_id}/merge", response_model=LineOut)
async def merge_line(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> LineOut:
    line = await lines_service.merge_with_next(session, room.id, line_id)
    return (await _enrich(session, room.id, [line]))[0]


@router.get("/{room_id}/speakers", response_model=list[SpeakerOut])
async def list_speakers(room: RoomDep, session: SessionDep) -> list[SpeakerOut]:
    return [SpeakerOut(**item) for item in await lines_service.list_speakers(session, room.id)]


@router.patch("/{room_id}/speakers/{speaker_key}", response_model=SpeakerOut)
async def patch_speaker(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    payload: SpeakerPatchIn,
    speaker_key: Annotated[str, PathParam(min_length=1, max_length=32)],
) -> SpeakerOut:
    """Переименовать спикера или объединить его с другим — одной транзакцией."""
    result = await lines_service.patch_speaker(
        session, room.id, speaker_key, label=payload.label, merge_into=payload.merge_into
    )
    for item in await lines_service.list_speakers(session, room.id):
        if item["speaker_key"] == result["speaker_key"]:
            return SpeakerOut(**item)
    return SpeakerOut(
        speaker_key=result["speaker_key"],
        speaker_label=result["label"] or result["speaker_key"],
        lines=result["lines"],
        total_ms=0,
    )


@router.get("/{room_id}/lines/{line_id}/original")
async def original_audio(
    request: Request,
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
    return file_response(
        request, path, content_type="audio/wav", filename=f"line-{line.idx}-original.wav"
    )
