"""Комнаты и участники."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import (
    OptionalParticipantDep,
    ParticipantDep,
    RoomDep,
    SessionDep,
    require_creator,
)
from app.core.errors import Forbidden
from app.core.config import settings
from app.schemas.room import (
    ParticipantCreate,
    ParticipantOut,
    ParticipantRegistered,
    ParticipantUpdate,
    RoomCreate,
    RoomCreated,
    RoomOut,
    RoomSettings,
)
from app.services import participants as participants_service
from app.services import rooms as rooms_service

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


async def _room_out(session, room) -> RoomOut:
    counters = await rooms_service.get_counters(session, room.id)
    video = await rooms_service.get_current_video(session, room)
    job = await rooms_service.get_latest_job(session, room.id)
    return RoomOut(
        id=room.id,
        title=room.title,
        status=room.status.value,
        settings=room.settings or {},
        created_at=room.created_at,
        expires_at=room.expires_at,
        counters=counters,
        video=(
            {
                "id": str(video.id),
                "duration_ms": video.duration_ms,
                "width": video.width,
                "height": video.height,
                "size_bytes": video.size_bytes,
                "has_audio": video.has_audio,
            }
            if video
            else None
        ),
        job=(
            {
                "id": str(job.id),
                "status": job.status.value,
                "stage": job.current_stage.value if job.current_stage else None,
                "progress": job.progress,
                "error": job.error,
            }
            if job
            else None
        ),
    )


@router.post("", response_model=RoomCreated, status_code=status.HTTP_201_CREATED)
async def create_room(payload: RoomCreate, session: SessionDep) -> RoomCreated:
    room = await rooms_service.create_room(session, payload.title)
    if payload.settings:
        validated = RoomSettings(**payload.settings).model_dump(exclude_none=True)
        room = await rooms_service.update_settings(session, room, validated)
    participant, token = await participants_service.register_participant(
        session, room, payload.display_name
    )
    await rooms_service.emit_event(session, room.id, "room.created", {"title": room.title})
    return RoomCreated(
        room=await _room_out(session, room),
        participant_id=participant.id,
        token=token,
        share_url=f"{settings.public_base_url.rstrip('/')}/room/{room.id}",
    )


@router.get("/{room_id}", response_model=RoomOut)
async def get_room(
    room: RoomDep, session: SessionDep, _: OptionalParticipantDep = None
) -> RoomOut:
    return await _room_out(session, room)


@router.patch("/{room_id}", response_model=RoomOut)
async def patch_room(
    room: RoomDep,
    session: SessionDep,
    payload: RoomSettings,
    participant: ParticipantDep,
) -> RoomOut:
    patch = payload.model_dump(exclude_none=True)
    room = await rooms_service.update_settings(session, room, patch)
    await rooms_service.emit_event(session, room.id, "room.settings", patch)
    return await _room_out(session, room)


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(room: RoomDep, session: SessionDep, participant: ParticipantDep) -> None:
    require_creator(room, participant)
    await rooms_service.soft_delete(session, room)


@router.post(
    "/{room_id}/participants",
    response_model=ParticipantRegistered,
    status_code=status.HTTP_201_CREATED,
)
async def register_participant(
    room: RoomDep, session: SessionDep, payload: ParticipantCreate
) -> ParticipantRegistered:
    participant, token = await participants_service.register_participant(
        session, room, payload.display_name
    )
    await rooms_service.emit_event(
        session,
        room.id,
        "participant.joined",
        {"participant_id": str(participant.id), "display_name": participant.display_name},
    )
    return ParticipantRegistered(
        participant_id=participant.id,
        token=token,
        display_name=participant.display_name,
        color=participant.color,
        is_creator=participant.is_creator,
    )


@router.get("/{room_id}/participants", response_model=list[ParticipantOut])
async def get_participants(room: RoomDep, session: SessionDep) -> list[ParticipantOut]:
    people = await participants_service.list_participants(session, room.id)
    return [ParticipantOut.model_validate(p) for p in people]


@router.patch("/{room_id}/participants/{participant_id}", response_model=ParticipantOut)
async def patch_participant(
    room: RoomDep,
    session: SessionDep,
    participant_id: uuid.UUID,
    payload: ParticipantUpdate,
    me: ParticipantDep,
) -> ParticipantOut:
    if participant_id != me.id:
        raise Forbidden("Можно менять только свой профиль", code="not_your_profile")
    participant = await participants_service.get_participant_or_404(
        session, room.id, participant_id
    )
    participant = await participants_service.rename_participant(
        session, participant, payload.display_name or participant.display_name, payload.color
    )
    await rooms_service.emit_event(
        session,
        room.id,
        "participant.updated",
        {"participant_id": str(participant.id), "display_name": participant.display_name},
    )
    return ParticipantOut.model_validate(participant)
