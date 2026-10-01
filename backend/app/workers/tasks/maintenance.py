"""Обслуживание очереди: реконсиляция зависших джобов.

Самопланирующийся актор (Dramatiq без beat — переотправляет себя с задержкой).
Лечит три ситуации:
 1. этап висит в RUNNING дольше лимита (воркер упал или его очередь никто не слушает —
    например, не запущен worker-gpu) -> этап и джоб помечаются FAILED с понятным кодом;
 2. джоб застрял в QUEUED и его первая задача потерялась -> переотправляем;
 3. джоб в RUNNING, но все его этапы уже закончились (рассинхрон) -> финализируем.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import dramatiq
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.models import JobStage, JobStatus, ProcessingJob, Room, RoomStatus, StageName, StageStatus
from app.services.jobs import STAGE_ORDER, stage_actor

log = get_logger("maintenance")


async def _reconcile() -> dict[str, int]:
    from app.core.db import worker_session
    from app.models import Room, RoomStatus
    from app.workers.tasks.purge_room import purge_room

    stats = {"stalled": 0, "requeued": 0, "finalized": 0, "purged": 0}
    now = datetime.now(UTC)
    stall = timedelta(minutes=settings.job_stage_stall_minutes)

    async with worker_session() as session:
        jobs = (
            (await session.execute(select(ProcessingJob).where(ProcessingJob.status.in_(
                [JobStatus.RUNNING, JobStatus.QUEUED]
            )))).scalars().all()
        )

        # Комнаты, застрявшие в удалении: purge мог не доехать (Redis был недоступен) или упасть
        # на файлах. Обычный срок жизни такие комнаты не подхватывает — они уже помечены
        # удалёнными, — поэтому без этого прохода видео и записи остались бы на диске навсегда.
        stuck = (
            await session.execute(
                select(Room).where(
                    Room.status == RoomStatus.DELETING,
                    Room.updated_at < now - timedelta(minutes=5),
                )
            )
        ).scalars().all()
        for room in stuck:
            room.status = RoomStatus.DELETING  # отметка времени: следующий заход не раньше 5 минут
            try:
                purge_room.send(room.id)
                stats["purged"] += 1
            except Exception as exc:  # noqa: BLE001
                log.error("purge_resend_failed", room_id=room.id, error=str(exc))
        if stuck:
            await session.commit()

        for job in jobs:
            stages = (
                (
                    await session.execute(
                        select(JobStage).where(JobStage.job_id == job.id).order_by(JobStage.created_at)
                    )
                )
                .scalars()
                .all()
            )
            running = [s for s in stages if s.status == StageStatus.RUNNING]

            # 1. зависший этап
            if running and any(s.started_at and now - s.started_at > stall for s in running):
                for stage in running:
                    if stage.started_at and now - stage.started_at > stall:
                        stage.status = StageStatus.FAILED
                        stage.finished_at = now
                        stage.error = {
                            "code": "stage_stalled",
                            "stage": stage.stage.value,
                            "message": (
                                f"Этап «{stage.stage.value}» не завершился за "
                                f"{settings.job_stage_stall_minutes} мин — вероятно, воркер для очереди "
                                f"не запущен (проверьте `docker compose ps`)"
                            ),
                        }
                job.status = JobStatus.FAILED
                job.finished_at = now
                job.error = {
                    "code": "stage_stalled",
                    "stage": running[0].stage.value,
                    "message": "Обработка прервана: этап слишком долго не отвечает",
                }
                stats["stalled"] += 1
                log.warning("job_stalled", job_id=str(job.id), stage=running[0].stage.value)
                continue

            # 2. потерянная задача: джоб в работе, но ни один этап не выполняется,
            #    а среди этапов есть незавершённые (сообщение потеряно или истекло по AgeLimit)
            if (
                job.status == JobStatus.RUNNING
                and not running
                and not all(s.status in (StageStatus.DONE, StageStatus.SKIPPED) for s in stages)
            ):
                pending = next(
                    (s for s in stages if s.status in (StageStatus.PENDING, StageStatus.RUNNING)),
                    None,
                )
                created = job.updated_at or job.created_at
                if pending is not None and created and now - created > stall:
                    try:
                        stage_actor(pending.stage.value).send(str(job.id))
                        stats["requeued"] += 1
                        log.warning(
                            "job_requeued_after_loss", job_id=str(job.id), stage=pending.stage.value
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.error("requeue_failed", job_id=str(job.id), error=str(exc))
                continue

            # 3. всё сделано, но джоб не закрыт
            if stages and all(
                s.status in (StageStatus.DONE, StageStatus.SKIPPED) for s in stages
            ):
                job.status = JobStatus.DONE
                job.progress = 100
                job.finished_at = now
                room = (await session.execute(select(Room).where(Room.id == job.room_id))).scalar_one_or_none()
                if room is not None:
                    room.status = RoomStatus.READY
                stats["finalized"] += 1
                continue

            # 3. потерянная задача в QUEUED
            if job.status == JobStatus.QUEUED and job.created_at and now - job.created_at > stall:
                first_pending = next(
                    (s for s in stages if s.status in (StageStatus.PENDING, StageStatus.RUNNING)),
                    None,
                )
                if first_pending is not None:
                    try:
                        stage_actor(first_pending.stage.value).send(str(job.id))
                        stats["requeued"] += 1
                        log.warning("job_requeued", job_id=str(job.id), stage=first_pending.stage.value)
                    except Exception as exc:  # noqa: BLE001
                        log.error("requeue_failed", job_id=str(job.id), error=str(exc))

        await session.commit()
    return stats


@dramatiq.actor(queue_name="system", max_retries=1, time_limit=120_000)
def reconcile_jobs() -> None:
    """Периодическая реконсиляция. Переотправляет себя через maintenance_interval_s."""
    try:
        stats = asyncio.run(_reconcile())
        if any(stats.values()):
            log.info("reconcile_done", **stats)
    except Exception as exc:  # noqa: BLE001 — обслуживание не должно умирать от одной ошибки
        log.error("reconcile_failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        reconcile_jobs.send_with_options(delay=settings.maintenance_interval_s * 1000)


def bootstrap() -> None:
    """Поставить первое сообщение реконсилятора (идемпотентно, один раз на установку)."""
    from app.core.redis import get_redis

    async def _once() -> bool:
        client = get_redis()
        if await client.set("maintenance:reconcile:bootstrapped", "1", nx=True):
            return True
        return False

    try:
        first = asyncio.run(_once())
        if first:
            reconcile_jobs.send_with_options(delay=settings.maintenance_interval_s * 1000)
            log.info("reconcile_bootstrapped")
    except Exception as exc:  # noqa: BLE001
        log.warning("reconcile_bootstrap_failed", error=str(exc))


__all__ = ["reconcile_jobs", "bootstrap", "STAGE_ORDER", "StageName"]
