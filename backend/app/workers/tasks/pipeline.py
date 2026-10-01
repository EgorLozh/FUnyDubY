"""Акторы конвейера обработки видео.

Каждый этап — отдельный актор: даёт per-stage retry, прогресс и возможность
перезапустить только один этап, не повторяя дорогие.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import dramatiq
from sqlalchemy import delete, select, text, update

from app.core.errors import DomainError, NotFound
from app.core.logging import get_logger
from app.models import DialogueLine, StageName
from app.services.jobs import StageEnv, StageNotImplemented, run_stage
from app.video import ffmpeg, probe

log = get_logger("pipeline")


class ArtifactsMissing(DomainError):
    status_code = 500
    code = "artifacts_missing"


# ---------------------------------------------------------------- extract_audio


@dramatiq.actor(queue_name="cpu", max_retries=2, time_limit=900_000)
def extract_audio(job_id: str) -> None:
    asyncio.run(run_stage(uuid.UUID(job_id), StageName.EXTRACT_AUDIO, _extract_audio))


async def _extract_audio(env: StageEnv, _session: Any) -> dict[str, Any]:
    source = env.source
    if not source.exists():
        raise NotFound("Исходный файл видео не найден", code="video_file_missing")

    stereo = env.path("audio", "mix.wav")
    mono = env.path("audio", "mix_mono16k.wav")

    await env.progress_cb(10)
    await asyncio.to_thread(ffmpeg.extract_audio, source, stereo, mono)
    await env.progress_cb(85)

    info = await asyncio.to_thread(probe.probe, stereo)
    return {
        "mix": env.rel("audio", "mix.wav"),
        "mix_mono16k": env.rel("audio", "mix_mono16k.wav"),
        "duration_ms": info.duration_ms,
        "sample_rate": 48000,
    }


# ------------------------------------------------------- ML-этапы (шаг 6 roadmap)


@dramatiq.actor(queue_name="gpu", max_retries=1, time_limit=900_000)
def separate_speech(job_id: str) -> None:
    asyncio.run(run_stage(uuid.UUID(job_id), StageName.SEPARATE_SPEECH, _separate_speech))


async def _separate_speech(env: StageEnv, _session: Any) -> dict[str, Any]:
    """Отделение речи (D1/D2) + формирование фона вычитанием с дуккингом."""
    from app.ml import separation

    mix = env.path("audio", "mix.wav")
    if not mix.exists():
        raise ArtifactsMissing("Нет audio/mix.wav — этап extract_audio не выполнен")

    speech_out = env.path("speech", "speech.wav")
    background_out = env.path("speech", "background.wav")
    await env.progress_cb(5)

    result = await asyncio.to_thread(
        separation.separate_file,
        mix,
        speech_out,
        background_out,
        mode=env.room_settings.get("separation_mode"),
        ducking_db=env.room_settings.get("ducking_db"),
        fallback=True,
    )
    await env.progress_cb(95)
    return {
        "speech": env.rel("speech", "speech.wav"),
        "background": env.rel("speech", "background.wav"),
        "backend": result.backend,
        "mode": result.mode,
        "alpha": result.alpha,
        "quality": result.quality,
    }


@dramatiq.actor(queue_name="gpu", max_retries=1, time_limit=900_000)
def transcribe(job_id: str) -> None:
    asyncio.run(run_stage(uuid.UUID(job_id), StageName.TRANSCRIBE, _transcribe))


async def _transcribe(env: StageEnv, _session: Any) -> dict[str, Any]:
    """Транскрипция дорожки речи в word-level JSON."""
    from app.ml import stt

    speech = env.path("speech", "speech.wav")
    if not speech.exists():
        raise ArtifactsMissing("Нет speech/speech.wav — этап отделения речи не выполнен")

    await env.progress_cb(5)
    result = await asyncio.to_thread(
        stt.transcribe_file,
        speech,
        env.path("dialogue", "words.json"),
        backend=env.room_settings.get("stt_backend"),
        language=(env.room_settings.get("stt_language") or None),
        initial_prompt=(env.room_settings.get("stt_prompt") or None),
    )
    await env.progress_cb(95)
    if not result.words:
        raise DomainError("Речь не распознана — возможно, в видео нет диалогов", code="stt_empty")
    return {
        "words": len(result.words),
        "language": result.language,
        "model": result.model,
        "backend": result.backend,
        "degraded": result.degraded,
    }


@dramatiq.actor(queue_name="gpu", max_retries=1, time_limit=900_000)
def diarize(job_id: str) -> None:
    asyncio.run(run_stage(uuid.UUID(job_id), StageName.DIARIZE, _diarize))


async def _diarize(env: StageEnv, _session: Any) -> dict[str, Any]:
    """Диаризация дорожки речи. Сбой деградирует (все Speaker 1), а не роняет джоб."""
    from app.ml import diarization

    speech = env.path("speech", "speech.wav")
    if not speech.exists():
        raise ArtifactsMissing("Нет speech/speech.wav — этап отделения речи не выполнен")

    max_speakers = env.room_settings.get("max_speakers") or None
    result = await asyncio.to_thread(
        diarization.diarize_file,
        speech,
        env.path("dialogue", "diarization.json"),
        num_speakers=int(max_speakers) if max_speakers else None,
        max_speakers=None,
        allow_degradation=True,
    )
    return {
        "turns": len(result.turns),
        "speakers": result.speakers,
        "backend": result.backend,
        "degraded": result.degraded,
        "reason": result.reason,
    }


# ---------------------------------------------------------------- merge_dialogue


@dramatiq.actor(queue_name="cpu", max_retries=1, time_limit=900_000)
def merge_dialogue(job_id: str) -> None:
    asyncio.run(run_stage(uuid.UUID(job_id), StageName.MERGE_DIALOGUE, _merge_dialogue))


async def _merge_dialogue(env: StageEnv, session: Any) -> dict[str, Any]:
    """Склейка слов и спикеров в реплики + нарезка фрагментов речи для прослушивания."""
    from app.core.config import settings
    from app.ml import merge as merge_module

    words_path = env.path("dialogue", "words.json")
    turns_path = env.path("dialogue", "diarization.json")
    if not words_path.exists() or not turns_path.exists():
        raise ArtifactsMissing(
            "Нет артефактов транскрипции и диаризации — этап нарезки запускается после них",
            extra={"words": words_path.exists(), "turns": turns_path.exists()},
        )

    words = [
        merge_module.Word(
            text=item["text"],
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            prob=item.get("prob"),
        )
        for item in json.loads(words_path.read_text("utf-8"))
    ]
    turns = [
        merge_module.Turn(speaker=item["speaker"], start_ms=int(item["start_ms"]), end_ms=int(item["end_ms"]))
        for item in json.loads(turns_path.read_text("utf-8"))
    ]

    settings_map = env.room_settings
    lines = merge_module.build_lines(
        words,
        turns,
        merge_gap_ms=int(settings_map.get("merge_gap_ms", settings.merge_gap_ms)),
        max_line_ms=int(settings_map.get("max_line_ms", settings.max_line_ms)),
    )
    await env.progress_cb(40)

    # Пересоздаём реплики, правки людей (is_edited) не трогаем.
    #
    # Порядок важен: сначала уводим нумерацию в «карантин», потом удаляем пересоздаваемые
    # реплики и только затем вставляем новые. Без карантина новая реплика с idx=2 падала на
    # уникальном индексе (room_id, idx), если правленая реплика человека занимала тот же idx:
    # сессия уходила в состояние «только откат», этап оставался RUNNING навсегда, а следующая
    # нарезка вообще не могла запуститься.
    quarantine = 100_000
    await session.execute(
        update(DialogueLine)
        .where(DialogueLine.room_id == env.room_id)
        .values(idx=DialogueLine.idx + quarantine)
    )
    await session.flush()
    await session.execute(
        delete(DialogueLine).where(
            DialogueLine.room_id == env.room_id, DialogueLine.is_edited.is_(False)
        )
    )
    await session.flush()

    speech_source = env.path("speech", "speech.wav")
    labels: dict[str, str] = {}
    for index, line in enumerate(lines):
        labels.setdefault(line.speaker_key, f"Speaker {len(labels) + 1}")
        row = DialogueLine(
            room_id=env.room_id,
            job_id=env.job_id,
            idx=index,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            speaker_key=line.speaker_key,
            speaker_label=labels[line.speaker_key],
            text=line.text,
            words=line.words,
            is_short=line.is_short,
            overlaps=line.overlaps,
        )
        session.add(row)

    await session.flush()
    await env.progress_cb(70)

    # Нарезка речи под каждую реплику (что пользователь слушает перед записью)
    segments_dir = env.path("speech", "segments")
    segments_dir.mkdir(parents=True, exist_ok=True)
    if speech_source.exists():
        rows = (
            (await session.execute(select(DialogueLine).where(DialogueLine.room_id == env.room_id)))
            .scalars()
            .all()
        )
        for position, row in enumerate(rows):
            target = segments_dir / f"{row.id}.wav"
            await asyncio.to_thread(
                ffmpeg.slice_wav,
                speech_source,
                target,
                max(0, row.start_ms - settings.segment_pad_ms),
                row.end_ms + settings.segment_pad_ms,
            )
            row.speech_path = env.rel("speech", "segments", f"{row.id}.wav")
            if position % 10 == 0:
                await env.progress_cb(70 + int(25 * position / max(1, len(rows))))
        await session.flush()

    # Перенумерация всех реплик комнаты по времени: правленые человеком реплики возвращаются
    # из карантина на своё место в таймлайне, новые встают рядом с ними.
    await session.execute(
        text(
            """
            WITH ordered AS (
                SELECT id, row_number() OVER (ORDER BY start_ms, idx) - 1 AS new_idx
                FROM dialogue_lines
                WHERE room_id = :room_id
            )
            UPDATE dialogue_lines AS line
            SET idx = ordered.new_idx
            FROM ordered
            WHERE line.id = ordered.id
            """
        ),
        {"room_id": env.room_id},
    )
    await session.flush()

    # Подчищаем нарезки прошлых прогонов. Реплики пересоздаются с новыми UUID, поэтому старые
    # файлы остаются сиротами: замерено 16 файлов / 17 МБ мусора в одной комнате после
    # нескольких запусков нарезки. Файлы сохраняем только для актуальных реплик (включая
    # правленые людьми — они переживают пересоздание).
    removed = 0
    if segments_dir.exists():
        keep = {
            f"{line_id}.wav"
            for (line_id,) in (
                await session.execute(
                    select(DialogueLine.id).where(DialogueLine.room_id == env.room_id)
                )
            ).all()
        }
        for candidate in segments_dir.glob("*.wav"):
            if candidate.name not in keep:
                candidate.unlink(missing_ok=True)
                removed += 1
        if removed:
            log.info("stale_segments_removed", room_id=env.room_id, count=removed)

    (env.path("dialogue", "lines.json")).write_text(
        json.dumps(
            [
                {
                    "idx": line_idx,
                    "speaker": line.speaker_key,
                    "start_ms": line.start_ms,
                    "end_ms": line.end_ms,
                    "text": line.text,
                }
                for line_idx, line in enumerate(lines)
            ],
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    return {
        "lines": len(lines),
        "speakers": len(labels),
        "segments_dir": env.rel("speech", "segments"),
        "stale_segments_removed": removed,
    }
