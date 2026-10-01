"""Сравнение бэкендов разделения речи на одном и том же файле.

Запуск (внутри worker-gpu):
    python scripts/check_separation.py [путь_к_wav] [--compare]

Без `--compare` считается только основной бэкенд (SEPARATION_MODEL).
С `--compare` тот же файл прогоняется и через fallback Demucs — чтобы числа метрики
были сравнимы, а не взяты из разных прогонов.

Метрика `speech_leak_db` — корреляционная проекция фона на оценку речи (см.
`app/ml/separation.py::speech_leak_db`). Порог деградации: выше ‑20 дБ остаток голоса
в фоне уже слышен.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app.core.config import settings
from app.ml import separation
from app.ml.audio_io import Audio, read

THRESHOLD_DB = -20.0


def _find_mix() -> Path:
    for pattern in ("mix*", "original*", "*.wav"):
        for path in sorted(Path("/data/rooms").glob(f"*/audio/{pattern}")):
            if path.is_file():
                return path
    raise SystemExit("Не найден тестовый микс в /data/rooms/*/audio/")


def _report(name: str, mix: Audio, result: separation.SeparationResult, elapsed: float) -> None:
    quality = result.quality
    verdict = "деградация" if quality.get("degraded") else "ок"
    print(f"\n--- {name} ({elapsed:.1f} с) ---")
    print(f"  режим: {result.mode}")
    print(f"  утечка речи в фон: {quality['speech_leak_db']:+.2f} дБ (порог {THRESHOLD_DB:+.0f}) → {verdict}")
    print(f"  уровень фона в зонах речи относительно остального: {quality['bg_level_in_speech_db']:+.2f} дБ")
    print(f"  α вычитания: {quality['alpha']}")
    if "stem_rms_db" in quality:
        print(f"  RMS по стемам: {quality['stem_rms_db']}")
    print(f"  RMS микса {quality['mix_rms_db']:+.2f} / речи {quality['speech_rms_db']:+.2f} / фона {quality['background_rms_db']:+.2f} дБ")


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    compare = "--compare" in sys.argv
    path = Path(args[0]) if args else _find_mix()

    print(f"файл: {path} ({path.stat().st_size / 1e6:.1f} МБ)")
    mix = read(path)
    print(f"микс: {mix.duration_s:.2f} с, {mix.sample_rate} Гц, каналов {mix.channels}")

    primary = settings.separation_model
    if primary == "bandit_v2":
        started = time.perf_counter()
        result = separation.from_stems(
            mix,
            separation._bandit_stems(mix),
            ducking_db=settings.bandit_ducking_db,
        )
        result.backend = "bandit_v2"
        _report("Bandit v2 (music+sfx, без вычитания)", mix, result, time.perf_counter() - started)
    else:
        started = time.perf_counter()
        speech = separation.BACKENDS["demucs"](mix)
        result = separation.subtract(
            mix, speech, mode=settings.separation_mode, ducking_db=settings.default_ducking_db
        )
        _report("Demucs (вычитание + дуккинг)", mix, result, time.perf_counter() - started)

    if compare and primary != "demucs":
        started = time.perf_counter()
        speech = separation.BACKENDS["demucs"](mix)
        result = separation.subtract(
            mix, speech, mode=settings.separation_mode, ducking_db=settings.default_ducking_db
        )
        _report("Demucs (вычитание + дуккинг)", mix, result, time.perf_counter() - started)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
