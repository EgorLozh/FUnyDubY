"""Speech-to-text: faster-whisper (основной, D4) с Parakeet как опцией.

Отдаёт word-level таймстемпы — на них держится нарезка реплик (§7.3):
границы берём по словам, а не по «сегментам Whisper», которые часто рвут фразу.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger

log = get_logger("ml.stt")


class SttFailed(DomainError):
    status_code = 500
    code = "stt_failed"


@dataclass
class TranscriptResult:
    words: list[dict]
    language: str | None
    duration_s: float
    backend: str
    model: str
    degraded: bool = False


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _transcribe_whisper(
    audio_path: Path,
    *,
    model_name: str,
    language: str | None,
    initial_prompt: str | None,
) -> TranscriptResult:
    from faster_whisper import WhisperModel

    device = _device()
    compute_type = "float16" if device == "cuda" else "int8"
    download_root = str(Path(settings.model_cache_dir) / "faster-whisper")
    model = WhisperModel(model_name, device=device, compute_type=compute_type, download_root=download_root)

    segments, info = model.transcribe(
        str(audio_path),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
        initial_prompt=initial_prompt or None,
    )

    words: list[dict] = []
    for segment in segments:
        for word in segment.words or []:
            text = (word.word or "").strip()
            if not text:
                continue
            words.append(
                {
                    "text": text,
                    "start_ms": int(round(word.start * 1000)),
                    "end_ms": int(round(word.end * 1000)),
                    "prob": round(float(word.probability), 4) if word.probability is not None else None,
                }
            )
    return TranscriptResult(
        words=words,
        language=getattr(info, "language", None),
        duration_s=float(getattr(info, "duration", 0.0) or 0.0),
        backend="whisper",
        model=model_name,
    )


def _transcribe_parakeet(
    audio_path: Path, *, language: str | None
) -> TranscriptResult:  # pragma: no cover — опция, требует nemo_toolkit
    try:
        import nemo.collections.asr as nemo_asr
    except ImportError as exc:
        raise SttFailed(
            "Бэкенд parakeet требует nemo_toolkit (не входит в образ по умолчанию). "
            "Установите его или верните STT_BACKEND=whisper",
            code="parakeet_not_installed",
        ) from exc

    model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")
    output = model.transcribe([str(audio_path)], timestamps=True)
    stamps = output[0].timestamp.get("word", [])
    words = [
        {
            "text": str(item.get("word", "")).strip(),
            "start_ms": int(round(float(item["start"]) * 1000)),
            "end_ms": int(round(float(item["end"]) * 1000)),
            "prob": None,
        }
        for item in stamps
        if str(item.get("word", "")).strip()
    ]
    return TranscriptResult(
        words=words, language=language, duration_s=0.0, backend="parakeet", model="parakeet-tdt-0.6b-v3"
    )


def transcribe_file(
    audio_path: Path,
    out_path: Path,
    *,
    backend: str | None = None,
    language: str | None = None,
    initial_prompt: str | None = None,
) -> TranscriptResult:
    """Транскрипция в word-level JSON. При OOM автоматически переходит на лёгкую модель."""
    backend = backend or settings.stt_backend
    model_name = settings.stt_model

    if backend == "parakeet":
        result = _transcribe_parakeet(audio_path, language=language)
    else:
        try:
            result = _transcribe_whisper(
                audio_path,
                model_name=model_name,
                language=language,
                initial_prompt=initial_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            out_of_memory = "out of memory" in message or "cuda" in message and "memory" in message
            if not out_of_memory or model_name == settings.stt_fallback_model:
                raise
            log.warning(
                "stt_fallback",
                from_model=model_name,
                to_model=settings.stt_fallback_model,
                error=str(exc),
            )
            result = _transcribe_whisper(
                audio_path,
                model_name=settings.stt_fallback_model,
                language=language,
                initial_prompt=initial_prompt,
            )
            result.degraded = True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.words, ensure_ascii=False), "utf-8")
    log.info(
        "stt_done",
        backend=result.backend,
        model=result.model,
        words=len(result.words),
        language=result.language,
        degraded=result.degraded,
    )
    return result
