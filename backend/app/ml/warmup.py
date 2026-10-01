"""Прогрев моделей: скачать веса в MODEL_CACHE_DIR до начала обработки.

Запуск: `make warmup` (или `docker compose run --rm worker-gpu python -m app.ml.warmup`).
Нужен HF_TOKEN для gated-модели диаризации; при отсутствии токена шаг пропускается
с предупреждением, а не падением.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("ml.warmup")


def _size_of(path: Path) -> str:
    if not path.exists():
        return "нет"
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return f"{total / 1e9:.2f} ГБ"


def warmup_whisper() -> dict[str, str]:
    from faster_whisper import WhisperModel

    started = time.time()
    download_root = str(Path(settings.model_cache_dir) / "faster-whisper")
    for model_name in {settings.stt_model, settings.stt_fallback_model}:
        log.info("whisper_downloading", model=model_name)
        WhisperModel(model_name, device="cpu", compute_type="int8", download_root=download_root)
    return {
        "models": f"{settings.stt_model}, {settings.stt_fallback_model}",
        "size": _size_of(Path(download_root)),
        "seconds": f"{time.time() - started:.0f}",
    }


def warmup_diarization() -> dict[str, str]:
    if not settings.hf_token:
        return {"status": "skipped: нет HF_TOKEN"}
    import torch
    from pyannote.audio import Pipeline

    started = time.time()
    Pipeline.from_pretrained(settings.diarization_model, token=settings.hf_token)
    return {
        "model": settings.diarization_model,
        "seconds": f"{time.time() - started:.0f}",
        "cuda": str(torch.cuda.is_available()),
    }


def warmup_separation() -> dict[str, str]:
    """Грузим модель разделения, попутно скачивая веса."""
    from app.core.config import settings as cfg

    started = time.time()
    if cfg.separation_model == "bandit_v2":
        weights = Path(cfg.model_cache_dir) / "bandit_v2"
        if not weights.exists():
            return {
                "status": "нет весов Bandit v2",
                "hint": f"положите веса в {weights} (Zenodo record 12701995) или используйте demucs",
            }
    from demucs.pretrained import get_model

    get_model("htdemucs")
    return {"model": "htdemucs (fallback D2)", "seconds": f"{time.time() - started:.0f}"}


def main() -> int:
    log.info("warmup_start", cache_dir=str(settings.model_cache_dir))
    steps = {
        "whisper": warmup_whisper,
        "separation": warmup_separation,
        "diarization": warmup_diarization,
    }
    exit_code = 0
    for name, step in steps.items():
        try:
            result = step()
            log.info("warmup_step", step=name, **result)
        except Exception as exc:  # noqa: BLE001 — отчёт по шагам, а не «упало всё»
            exit_code = 1
            log.error("warmup_step_failed", step=name, error=f"{type(exc).__name__}: {exc}")
    log.info("warmup_done", status="ok" if exit_code == 0 else "partial")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
