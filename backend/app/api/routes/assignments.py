"""Назначение реплик: взять, освободить, продлить, захватить пачкой.

Роли в комнате равны, поэтому участник может назначить реплику и себе, и другому
(`participant_id` в теле) — это ровно тот UX «мы договорились голосом, кто что читает»,
который заложен в архитектуре: сложных ролей и модерации нет.

Конфликты возвращаются как 409 с кодом `line_taken` и именем того, кто держит реплику:
UI показывает «взял(а) Аня» вместо непонятной ошибки.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path as PathParam, Response, status

from app.api.deps import ParticipantDep, RoomDep, SessionDep
from app.schemas.line import (
    AssignmentIn,
    AssignmentOut,
    BulkAssignmentIn,
    BulkAssignmentOut,
)
from app.services import assignments as assignments_service

router = APIRouter(prefix="/api/rooms", tags=["assignments"])


def _to_out(assignment, names: dict[uuid.UUID, str]) -> AssignmentOut:
    return AssignmentOut(
        line_id=assignment.line_id,
        participant_id=assignment.participant_id,
        display_name=names.get(assignment.participant_id),
        assigned_at=assignment.assigned_at,
        expires_at=assignment.expires_at,
        version=assignment.version,
    )


@router.put("/{room_id}/lines/{line_id}/assignment", response_model=AssignmentOut)
async def claim_line(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
    payload: AssignmentIn | None = None,
) -> AssignmentOut:
    """Взять реплику. Повторный вызов тем же участником идемпотентен."""
    target = (payload.participant_id if payload else None) or participant.id
    assignment, _ = await assignments_service.claim(
        session, room.id, line_id, target, expected_version=payload.expected_version if payload else None
    )
    names = await assignments_service.participant_names(session, room.id)
    return _to_out(assignment, names)


@router.delete(
    "/{room_id}/lines/{line_id}/assignment", status_code=status.HTTP_204_NO_CONTENT
)
async def release_line(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> Response:
    await assignments_service.release(session, room.id, line_id, participant_id=participant.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{room_id}/lines/{line_id}/assignment/heartbeat", response_model=AssignmentOut)
async def heartbeat(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    line_id: Annotated[uuid.UUID, PathParam()],
) -> AssignmentOut:
    """Продлить захват, пока участник работает над репликой."""
    assignment = await assignments_service.heartbeat(session, room.id, line_id, participant.id)
    names = await assignments_service.participant_names(session, room.id)
    return _to_out(assignment, names)


@router.post("/{room_id}/assignments/bulk", response_model=BulkAssignmentOut)
async def bulk_claim(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    payload: BulkAssignmentIn,
) -> BulkAssignmentOut:
    """Захватить пачку реплик: «все реплики этого спикера» или «все свободные»."""
    target = payload.participant_id or participant.id
    result = await assignments_service.bulk_claim(
        session,
        room.id,
        target,
        speaker_key=payload.speaker_key,
        line_ids=payload.line_ids,
        scope=payload.scope,
    )
    return BulkAssignmentOut(**result)
