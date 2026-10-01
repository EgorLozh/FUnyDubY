"""Диаризация: pyannote community-1 (D5) с fallback на Sortformer и деградацией.

Эксклюзивный режим pyannote («в каждый момент активен один спикер») снимает
основную сложность склейки слов со спикерами (§7.3).
Если диаризация недоступна (нет токена/условий на HF), этап не роняет обработку:
пишется пустой список turns, и все реплики получают метку Speaker 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger

log = get_logger("ml.diarization")


class DiarizationFailed(DomainError):
    status_code = 500
    code = "diarization_failed"


@dataclass
class DiarizationResult:
    turns: list[dict[str, Any]]
    speakers: int
    backend: str
    degraded: bool = False
    reason: str | None = None


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _pyannote_turns(
    audio_path: Path, *, num_speakers: int | None, min_speakers: int | None, max_speakers: int | None
) -> list[dict[str, Any]]:
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(settings.diarization_model, token=settings.hf_token or None)
    pipeline.to(torch.device(_device()))

    params: dict[str, int] = {}
    if num_speakers:
        params["num_speakers"] = int(num_speakers)
    else:
        if min_speakers:
            params["min_speakers"] = int(min_speakers)
        if max_speakers:
            params["max_speakers"] = int(max_speakers)

    output = pipeline(str(audio_path), **params)
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", None)
    if annotation is None:
        raise DiarizationFailed("pyannote вернул неожиданный формат результата")

    turns: list[dict[str, Any]] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append(
            {
                "speaker": _normalize_speaker(str(speaker)),
                "start_ms": int(round(turn.start * 1000)),
                "end_ms": int(round(turn.end * 1000)),
                "exclusive": True,
            }
        )
    return turns


def _normalize_speaker(raw: str) -> str:
    """pyannote отдаёт SPEAKER_00 — приводим к стабильному ключу spk_0."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"spk_{int(digits)}" if digits else f"spk_{abs(hash(raw)) % 1000}"


def diarize_file(
    audio_path: Path,
    out_path: Path,
    *,
    num_speakers: int | None = None,
    max_speakers: int | None = None,
    allow_degradation: bool = True,
) -> DiarizationResult:
    """Диаризация в JSON-список turns. При сбое — деградация вместо падения этапа."""
    if not settings.hf_token:
        if not allow_degradation:
            raise DiarizationFailed(
                "Нужен HF_TOKEN и принятые условия на pyannote/speaker-diarization-community-1",
                code="hf_token_missing",
            )
        result = DiarizationResult(
            turns=[],
            speakers=1,
            backend="none",
            degraded=True,
            reason="HF_TOKEN не задан — спикеры не определялись, все реплики получили Speaker 1",
        )
        _write(out_path, result)
        return result

    try:
        turns = _pyannote_turns(
            audio_path,
            num_speakers=num_speakers,
            min_speakers=None,
            max_speakers=max_speakers,
        )
        speakers = len({turn["speaker"] for turn in turns}) or 1
        result = DiarizationResult(turns=turns, speakers=speakers, backend="pyannote-community-1")
    except Exception as exc:  # noqa: BLE001
        log.error("diarization_failed", error=f"{type(exc).__name__}: {exc}")
        if not allow_degradation:
            raise
        result = DiarizationResult(
            turns=[],
            speakers=1,
            backend="none",
            degraded=True,
            reason=f"Диаризация не удалась ({type(exc).__name__}); все реплики получили Speaker 1",
        )

    _write(out_path, result)
    log.info(
        "diarization_done",
        turns=len(result.turns),
        speakers=result.speakers,
        degraded=result.degraded,
    )
    return result


def _write(out_path: Path, result: DiarizationResult) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.turns, ensure_ascii=False), "utf-8")
