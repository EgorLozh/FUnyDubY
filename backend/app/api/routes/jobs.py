"""Джобы обработки: постановка, статус, отмена."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import ParticipantDep, RoomDep, SessionDep
from app.schemas.job import JobCreate, JobOut, StageOut
from app.services import jobs as jobs_service
from app.services import video as video_service

router = APIRouter(prefix="/api/rooms", tags=["jobs"])


async def _job_out(session, job) -> JobOut:
    stages = await jobs_service.list_stages(session, job.id)
    return JobOut(
        id=job.id,
        room_id=job.room_id,
        status=job.status.value,
        current_stage=job.current_stage.value if job.current_stage else None,
        progress=job.progress,
        scope=job.scope,
        attempt=job.attempt,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        stages=[
            StageOut(
                stage=s.stage.value,
                status=s.status.value,
                attempt=s.attempt,
                progress=s.progress,
                duration_ms=s.duration_ms,
                metrics=s.metrics,
                artifacts=s.artifacts,
                error=s.error,
            )
            for s in stages
        ],
    )


@router.post(
    "/{room_id}/jobs",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_job(
    room: RoomDep, session: SessionDep, participant: ParticipantDep, payload: JobCreate
) -> JobOut:
    """Запустить обработку (или перезапустить конкретный этап). Идемпотентно по видео."""
    video = await video_service.get_video_or_404(session, room)
    job = await jobs_service.create_or_reset_job(
        session, room, video, scope=payload.scope, force=payload.force
    )
    if job.status.value == "QUEUED":
        await jobs_service.enqueue_first_stage(job.id)
    return await _job_out(session, job)


@router.get("/{room_id}/jobs", response_model=list[JobOut])
async def list_jobs(room: RoomDep, session: SessionDep) -> list[JobOut]:
    from sqlalchemy import select

    from app.models import ProcessingJob

    rows = (
        (
            await session.execute(
                select(ProcessingJob)
                .where(ProcessingJob.room_id == room.id)
                .order_by(ProcessingJob.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return [await _job_out(session, job) for job in rows]


@router.get("/{room_id}/jobs/{job_id}", response_model=JobOut)
async def get_job(
    room: RoomDep,
    session: SessionDep,
    job_id: Annotated[uuid.UUID, Path()],
) -> JobOut:
    job = await jobs_service.get_job(session, room.id, job_id)
    return await _job_out(session, job)


@router.post("/{room_id}/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    room: RoomDep,
    session: SessionDep,
    participant: ParticipantDep,
    job_id: Annotated[uuid.UUID, Path()],
) -> JobOut:
    job = await jobs_service.get_job(session, room.id, job_id)
    job = await jobs_service.cancel_job(session, job)
    return await _job_out(session, job)
