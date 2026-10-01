"""Жизненный цикл джоба обработки: этапы, прогресс, идемпотентность, запуск следующего этапа."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, DomainError, NotFound
from app.core.logging import get_logger
from app.core.redis import get_redis, room_channel
from app.core.security import room_relative
from app.models import (
    JobStage,
    JobStatus,
    ProcessingJob,
    Room,
    RoomStatus,
    StageName,
    StageStatus,
    STAGE_WEIGHTS,
    Video,
)

log = get_logger("jobs")

STAGE_ORDER: list[StageName] = [
    StageName.EXTRACT_AUDIO,
    StageName.SEPARATE_SPEECH,
    StageName.TRANSCRIBE,
    StageName.DIARIZE,
    StageName.MERGE_DIALOGUE,
]

STAGE_ACTORS = {
    StageName.EXTRACT_AUDIO.value: "extract_audio",
    StageName.SEPARATE_SPEECH.value: "separate_speech",
    StageName.TRANSCRIBE.value: "transcribe",
    StageName.DIARIZE.value: "diarize",
    StageName.MERGE_DIALOGUE.value: "merge_dialogue",
}

# Очереди: ffmpeg и чистые алгоритмы — CPU, модели — GPU (сериализуются на один слот)
STAGE_QUEUES = {
    StageName.EXTRACT_AUDIO.value: "cpu",
    StageName.SEPARATE_SPEECH.value: "gpu",
    StageName.TRANSCRIBE.value: "gpu",
    StageName.DIARIZE.value: "gpu",
    StageName.MERGE_DIALOGUE.value: "cpu",
}

# Ошибки, которые бессмысленно повторять: формат, отсутствие звука, слишком длинное видео
TERMINAL_CODES = {"unsupported_format", "no_audio", "video_too_long", "corrupt_media", "no_video_stream"}


class StageNotImplemented(DomainError):
    """Этап объявлен, но реализация появится на следующем шаге roadmap."""

    status_code = 501
    code = "stage_not_implemented"


@dataclass
class StageEnv:
    """Всё, что нужно этапу: пути, метаданные и колбэк прогресса."""

    room_id: str
    job_id: uuid.UUID
    video: Video
    room_dir: Path
    room_settings: dict[str, Any]
    progress_cb: Callable[[int], Awaitable[None]]

    @property
    def source(self) -> Path:
        from app.services import storage

        return storage.absolute(self.video.source_path)

    def path(self, *parts: str) -> Path:
        return self.room_dir.joinpath(*parts)

    def rel(self, *parts: str) -> str:
        return room_relative(self.room_id, *parts)


async def get_job(session: AsyncSession, room_id: str, job_id: uuid.UUID) -> ProcessingJob:
    job = (
        await session.execute(
            select(ProcessingJob).where(ProcessingJob.id == job_id, ProcessingJob.room_id == room_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFound("Задача обработки не найдена", code="job_not_found")
    return job


async def list_stages(session: AsyncSession, job_id: uuid.UUID) -> list[JobStage]:
    return list(
        (
            await session.execute(
                select(JobStage).where(JobStage.job_id == job_id).order_by(JobStage.created_at)
            )
        )
        .scalars()
        .all()
    )


async def create_or_reset_job(
    session: AsyncSession, room: Room, video: Video, scope: str = "all"
) -> ProcessingJob:
    """Создать джоб или переиспользовать существующий (идемпотентно, unique по video_id)."""
    existing = (
        await session.execute(select(ProcessingJob).where(ProcessingJob.video_id == video.id))
    ).scalar_one_or_none()

    if existing and existing.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return existing

    if existing is None:
        job = ProcessingJob(
            room_id=room.id,
            video_id=video.id,
            status=JobStatus.QUEUED,
            scope=scope,
            progress=0,
            current_stage=None,
        )
        session.add(job)
        await session.flush()
    else:
        job = existing
        job.status = JobStatus.QUEUED
        job.scope = scope
        job.progress = 0
        job.current_stage = None
        job.error = None
        job.attempt += 1
        job.started_at = None
        job.finished_at = None
        await session.flush()

    # Явный scope перезапускает указанный этап И все последующие: их входные данные меняются
    # (например, после повторной диаризации нарезку реплик нужно пересобрать).
    if scope == "all":
        stages_to_reset = list(STAGE_ORDER)
        force = False
    else:
        index = next((i for i, s in enumerate(STAGE_ORDER) if s.value == scope), None)
        if index is None:
            raise Conflict(f"Неизвестный этап: {scope}", code="unknown_stage")
        stages_to_reset = STAGE_ORDER[index:]
        force = True

    for stage in stages_to_reset:
        row = (
            await session.execute(
                select(JobStage).where(JobStage.job_id == job.id, JobStage.stage == stage)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                JobStage(
                    job_id=job.id,
                    stage=stage,
                    status=StageStatus.PENDING,
                    attempt=1,
                    progress=0,
                )
            )
        elif force or row.status != StageStatus.DONE:
            row.status = StageStatus.PENDING
            row.progress = 0
            row.error = None
            row.attempt += 1

    room.status = RoomStatus.PROCESSING
    await session.commit()
    log.info("job_created", job_id=str(job.id), room_id=room.id, scope=scope, attempt=job.attempt)
    return job


async def _publish(room_id: str, payload: dict[str, Any]) -> None:
    try:
        await get_redis().publish(room_channel(room_id), json.dumps(payload))
    except Exception as exc:  # noqa: BLE001
        log.warning("event_publish_failed", room_id=room_id, error=str(exc))


async def _update_job_progress(session: AsyncSession, job: ProcessingJob) -> int:
    stages = await list_stages(session, job.id)
    total_weight = sum(STAGE_WEIGHTS.get(s.stage.value, 0) for s in stages) or 1
    done_weight = 0
    for stage in stages:
        weight = STAGE_WEIGHTS.get(stage.stage.value, 0)
        if stage.status in (StageStatus.DONE, StageStatus.SKIPPED):
            done_weight += weight
        elif stage.status == StageStatus.RUNNING:
            done_weight += weight * (stage.progress / 100)
    job.progress = int(min(99, round(done_weight / total_weight * 100)))
    return job.progress


async def run_stage(
    job_id: uuid.UUID,
    stage: StageName,
    handler: Callable[[StageEnv, AsyncSession], Awaitable[dict[str, Any]]],
    *,
    next_actor: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Универсальный раннер этапа: идемпотентность, прогресс, ошибки, запуск следующего этапа."""
    from app.core.db import worker_session

    async with worker_session() as session:
        job = (
            await session.execute(select(ProcessingJob).where(ProcessingJob.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            log.warning("stage_job_missing", job_id=str(job_id))
            return {"skipped": "job_not_found"}

        video = (
            await session.execute(select(Video).where(Video.id == job.video_id))
        ).scalar_one_or_none()
        if video is None:
            return {"skipped": "video_not_found"}

        room_row = (
            await session.execute(select(Room).where(Room.id == job.room_id))
        ).scalar_one_or_none()
        room_settings = dict(room_row.settings or {}) if room_row else {}

        stage_row = (
            await session.execute(
                select(JobStage)
                .where(JobStage.job_id == job.id, JobStage.stage == stage)
                .order_by(JobStage.attempt.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if stage_row is None:
            stage_row = JobStage(job_id=job.id, stage=stage, status=StageStatus.PENDING)
            session.add(stage_row)
            await session.flush()

        # Идемпотентность: повторная доставка сообщения не переделывает готовый этап
        if stage_row.status in (StageStatus.DONE, StageStatus.SKIPPED):
            log.info("stage_skipped", job_id=str(job.id), stage=stage.value)
            await _enqueue_next(job, stage, next_actor)
            return {"skipped": stage.value}

        started = datetime.now(UTC)
        stage_row.status = StageStatus.RUNNING
        stage_row.started_at = started
        stage_row.progress = 0
        job.status = JobStatus.RUNNING
        job.current_stage = stage
        job.started_at = job.started_at or started
        await session.commit()
        await _publish(
            job.room_id, {"type": "job.stage", "stage": stage.value, "status": "RUNNING"}
        )

        async def progress_cb(percent: int) -> None:
            stage_row.progress = max(0, min(100, percent))
            await _update_job_progress(session, job)
            await session.commit()
            await _publish(
                job.room_id,
                {
                    "type": "job.progress",
                    "stage": stage.value,
                    "stage_progress": stage_row.progress,
                    "progress": job.progress,
                },
            )

        env = StageEnv(
            room_id=job.room_id,
            job_id=job.id,
            video=video,
            room_dir=settings.rooms_root / job.room_id,
            room_settings=dict(room_settings or {}),
            progress_cb=progress_cb,
        )

        try:
            artifacts = await handler(env, session)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", type(exc).__name__)
            detail = getattr(exc, "detail", str(exc)) or type(exc).__name__
            stage_row.status = StageStatus.FAILED
            stage_row.error = {"code": code, "message": detail, "stage": stage.value}
            stage_row.finished_at = datetime.now(UTC)
            job.status = JobStatus.FAILED
            job.error = {"code": code, "message": detail, "stage": stage.value}
            job.finished_at = datetime.now(UTC)
            await session.commit()
            await _publish(
                job.room_id,
                {"type": "job.failed", "stage": stage.value, "code": code, "message": detail},
            )
            log.error("stage_failed", job_id=str(job.id), stage=stage.value, code=code)
            raise

        stage_row.status = StageStatus.DONE
        stage_row.progress = 100
        stage_row.artifacts = artifacts
        stage_row.finished_at = datetime.now(UTC)
        if stage_row.started_at:
            stage_row.duration_ms = int(
                (stage_row.finished_at - stage_row.started_at).total_seconds() * 1000
            )
        await _update_job_progress(session, job)
        await session.commit()
        await _publish(
            job.room_id,
            {"type": "job.stage", "stage": stage.value, "status": "DONE", "artifacts": artifacts},
        )
        log.info(
            "stage_done",
            job_id=str(job.id),
            stage=stage.value,
            duration_ms=stage_row.duration_ms,
        )

        await _enqueue_next(job, stage, next_actor)
        return artifacts or {}


async def _enqueue_next(
    job: ProcessingJob, stage: StageName, next_actor: Callable[[str], Any] | None
) -> None:
    """Запустить следующий этап; после последнего — перевести комнату в READY."""
    if next_actor is not None:
        next_actor(str(job.id))
        return

    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        from app.core.db import worker_session
        from app.services.rooms import emit_event

        async with worker_session() as session:
            room = (await session.execute(select(Room).where(Room.id == job.room_id))).scalar_one()
            job_row = (
                await session.execute(select(ProcessingJob).where(ProcessingJob.id == job.id))
            ).scalar_one()
            job_row.status = JobStatus.DONE
            job_row.progress = 100
            job_row.finished_at = datetime.now(UTC)
            room.status = RoomStatus.READY
            await session.commit()
            await emit_event(session, room.id, "job.done", {"job_id": str(job.id)})
        log.info("job_done", job_id=str(job.id), room_id=job.room_id)
        return

    next_stage = STAGE_ORDER[index + 1]
    _send(STAGE_ACTORS[next_stage.value], str(job.id), queue=STAGE_QUEUES[next_stage.value])


def _send(actor_name: str, arg: str, queue: str) -> None:
    """Отправить задачу по имени актора.

    Импорт `app.workers.tasks` регистрирует акторы в брокере; очередь определяется
    самим актором (queue_name), параметр нужен для явности в вызывающем коде.
    """
    import dramatiq

    from app.workers import tasks  # noqa: F401 — регистрирует акторы

    actor = dramatiq.get_broker().get_actor(actor_name)
    if actor is None:
        raise Conflict(f"Актор {actor_name} не зарегистрирован", code="actor_missing")
    actor.send(arg)


async def enqueue_first_stage(job_id: uuid.UUID, queue: str = "cpu") -> None:
    """Поставить в очередь первый этап конвейера (или с указанного в scope)."""
    _send("extract_audio", str(job_id), queue=queue)


def stage_actor(stage: str):
    """Получить актор этапа (используется реконсилятором для переотправки)."""
    import dramatiq

    from app.workers import tasks  # noqa: F401 — регистрирует акторы

    actor = dramatiq.get_broker().get_actor(STAGE_ACTORS[stage])
    if actor is None:
        raise Conflict(f"Актор {STAGE_ACTORS[stage]} не зарегистрирован", code="actor_missing")
    return actor


async def cancel_job(session: AsyncSession, job: ProcessingJob) -> ProcessingJob:
    job.status = JobStatus.CANCELED
    job.finished_at = datetime.now(UTC)
    stages = await list_stages(session, job.id)
    for stage in stages:
        if stage.status in (StageStatus.PENDING, StageStatus.RUNNING):
            stage.status = StageStatus.CANCELED
    await session.commit()
    await get_redis().set(f"job:{job.id}:canceled", "1", ex=86400)
    await _publish(job.room_id, {"type": "job.canceled", "job_id": str(job.id)})
    return job


async def is_canceled(job_id: uuid.UUID) -> bool:
    try:
        return bool(await get_redis().exists(f"job:{job_id}:canceled"))
    except Exception:  # noqa: BLE001
        return False
