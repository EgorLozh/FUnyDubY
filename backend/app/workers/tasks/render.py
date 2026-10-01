"""render_final: сборка итогового видео (фон + записанные тейки) на очереди `cpu`.

Рендер идёт в три шага со своими статусами (`MIXING` -> `ENCODING` -> `DONE`), чтобы интерфейс
показывал, что именно происходит, а не абстрактный процент. Постоянные ошибки (нет видео,
нечего собирать, файл тейка пропал) не уходят в повтор Dramatiq — они помечают рендер `FAILED`
с понятным кодом.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import dramatiq
from sqlalchemy import select

from app.core.config import settings
from app.core.db import WorkerSessionFactory
from app.core.logging import get_logger
from app.ml import audio_io
from app.ml import mix as mixer
from app.ml.audio_io import read
from app.models import DialogueLine, Recording, RenderJob, RenderStatus, Room, Video
from app.services import storage
from app.services.rooms import emit_event
from app.video import ffmpeg, probe

log = get_logger("render")


def _now() -> datetime:
    return datetime.now(UTC)


async def _set(session, render: RenderJob, status: RenderStatus, progress: int, **fields) -> None:
    render.status = status
    render.progress = max(0, min(100, progress))
    for key, value in fields.items():
        setattr(render, key, value)
    await session.commit()
    await emit_event(
        session,
        render.room_id,
        "render.updated",
        {"render_id": str(render.id), "status": status.value, "progress": render.progress},
    )


async def _render(render_id: uuid.UUID) -> dict[str, object]:
    async with WorkerSessionFactory() as session:
        render = await session.get(RenderJob, render_id)
        if render is None:
            log.warning("render_missing", render_id=str(render_id))
            return {"status": "missing"}
        if render.status == RenderStatus.CANCELED:
            log.info("render_skipped_canceled", render_id=str(render_id))
            return {"status": "canceled"}

        room = await session.get(Room, render.room_id)
        video = await session.get(Video, room.video_id) if room and room.video_id else None
        if video is None:
            await _set(session, render, RenderStatus.FAILED, 0, error={
                "code": "no_video", "message": "Исходное видео недоступно",
            }, finished_at=_now())
            return {"status": "failed", "error": "no_video"}

        lines = (
            await session.scalars(
                select(DialogueLine)
                .where(DialogueLine.room_id == render.room_id)
                .order_by(DialogueLine.start_ms)
            )
        ).all()
        recordings = (
            await session.scalars(
                select(Recording).where(
                    Recording.line_id.in_([line.id for line in lines]),
                    Recording.is_current.is_(True),
                    Recording.orphaned.is_(False),
                )
            )
        ).all()
        by_line = {rec.line_id: rec for rec in recordings}

        # ── 1. Микс ────────────────────────────────────────────────────────────────
        await _set(session, render, RenderStatus.MIXING, 10)
        render.video_path = video.source_path
        background_path = storage.absolute(f"rooms/{render.room_id}/speech/background.wav")
        if not background_path.exists():
            await _set(session, render, RenderStatus.FAILED, 0, error={
                "code": "artifacts_missing",
                "message": "Нет фона (этап разделения речи не пройден)",
            }, finished_at=_now())
            return {"status": "failed", "error": "artifacts_missing"}

        unrecorded = str(render.options.get("unrecorded", settings.unrecorded_policy))
        slots: list[mixer.MixSlot] = []
        for line in lines:
            record = by_line.get(line.id)
            if record is not None and record.processed_path:
                slots.append(
                    mixer.MixSlot(
                        line_id=str(line.id),
                        start_ms=line.start_ms,
                        end_ms=line.end_ms,
                        source=storage.absolute(record.processed_path),
                        kind="take",
                    )
                )
            elif line.keep_original or unrecorded == "original":
                source = storage.absolute(line.speech_path) if line.speech_path else None
                slots.append(
                    mixer.MixSlot(
                        line_id=str(line.id),
                        start_ms=line.start_ms,
                        end_ms=line.end_ms,
                        source=source if source and source.exists() else None,
                        kind="original" if source else "silent",
                    )
                )
            else:
                slots.append(
                    mixer.MixSlot(
                        line_id=str(line.id), start_ms=line.start_ms, end_ms=line.end_ms,
                        source=None, kind="silent",
                    )
                )

        background = await asyncio.to_thread(read, background_path)
        result = await asyncio.to_thread(mixer.build_mix, background, slots)
        out_dir = storage.absolute(f"rooms/{render.room_id}/rendered/{render.id}")
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_path = out_dir / "audio.wav"
        await asyncio.to_thread(audio_io.write, audio_path, result.audio)

        loudness = await asyncio.to_thread(mixer_measure, audio_path)
        render.audio_mix_path = storage.relative(audio_path)
        metrics = {
            "used_takes": result.used_takes,
            "used_originals": result.used_originals,
            "silent_lines": result.silent_lines,
            "applied_gain_db": result.applied_gain_db,
            "peak_db": result.peak_db,
            "loudness_lufs": loudness,
            "slot_problems": result.slot_problems[:10],
        }
        await _set(
            session, render, RenderStatus.MIXING, 60, metrics=metrics,
            used_lines=result.used_takes,
        )

        if result.used_takes == 0:
            await _set(session, render, RenderStatus.FAILED, 60, error={
                "code": "nothing_recorded",
                "message": "Ни один тейк не удалось использовать",
            }, finished_at=_now())
            return {"status": "failed", "error": "nothing_recorded"}

        # ── 2. Мультиплекс с видео (картинка копируется без перекодирования) ──────
        await _set(session, render, RenderStatus.ENCODING, 75)
        output = out_dir / "final.mp4"
        await asyncio.to_thread(
            ffmpeg.mux_video_audio, Path(video.source_path), audio_path, output
        )

        info = await asyncio.to_thread(probe.probe, output)
        if info.duration_ms <= 0 or output.stat().st_size == 0:
            await _set(session, render, RenderStatus.FAILED, 75, error={
                "code": "render_corrupt",
                "message": "Итоговый файл пуст или повреждён",
            }, finished_at=_now())
            return {"status": "failed", "error": "render_corrupt"}

        # ── 3. Готово: актуальный результат комнаты ──────────────────────────────
        from app.services.renders import mark_current

        render.output_path = storage.relative(output)
        render.size_bytes = output.stat().st_size
        render.duration_ms = info.duration_ms
        render.error = None
        await _set(session, render, RenderStatus.DONE, 100, finished_at=_now())
        await mark_current(session, render)
        await session.commit()

        log.info(
            "render_done",
            room_id=render.room_id,
            render_id=str(render.id),
            size_bytes=render.size_bytes,
            duration_ms=render.duration_ms,
            used_takes=result.used_takes,
            applied_gain_db=result.applied_gain_db,
            loudness=loudness,
        )
        return {
            "status": "done",
            "size_bytes": render.size_bytes,
            "duration_ms": render.duration_ms,
            "used_takes": result.used_takes,
        }


def mixer_measure(path: Path) -> float | None:
    """Громкость готового микса (EBU R128) — пишем в метрики для аудита."""
    return ffmpeg.loudness_lufs(path)


async def _guard(render_id: uuid.UUID) -> dict[str, object]:
    """Пометить рендер упавшим, что бы ни случилось по пути."""
    async with WorkerSessionFactory() as session:
        render = await session.get(RenderJob, render_id)
        if render is None:
            return {"status": "missing"}
        if render.status in (RenderStatus.DONE, RenderStatus.CANCELED):
            return {"status": render.status.value}
        render.status = RenderStatus.FAILED
        render.finished_at = _now()
        render.error = {"code": "render_failed", "message": "Внутренняя ошибка сборки"}
        await session.commit()
        return {"status": "failed"}


@dramatiq.actor(queue_name="cpu", max_retries=0, time_limit=30 * 60 * 1000)
def render_final(render_id: str) -> None:
    """Собрать итоговое видео комнаты."""
    identifier = uuid.UUID(render_id)
    try:
        asyncio.run(_render(identifier))
    except Exception as exc:  # noqa: BLE001 — рендер не должен падать молча
        log.error("render_failed", render_id=render_id, error=str(exc))
        asyncio.run(_guard(identifier))
        raise
