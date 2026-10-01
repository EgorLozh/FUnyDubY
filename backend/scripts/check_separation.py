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

import numpy as np

from app.core.config import settings
from app.ml import separation
from app.ml.audio_io import Audio, read


def _find_mix() -> Path:
    for pattern in ("mix*", "original*", "*.wav"):
        for path in sorted(Path("/data/rooms").glob(f"*/audio/{pattern}")):
            if path.is_file():
                return path
    raise SystemExit("Не найден тестовый микс в /data/rooms/*/audio/")


def _report(
    name: str,
    mix: Audio,
    result: separation.SeparationResult,
    elapsed: float,
    truth: Audio | None = None,
) -> None:
    quality = result.quality
    verdict = "деградация" if quality.get("degraded") else "ок"
    print(f"\n--- {name} ({elapsed:.1f} с) ---")
    print(f"  режим: {result.mode}")
    print(
        f"  уровень фона в зонах речи относительно остального: "
        f"{quality['speech_window_gain_db']:+.2f} дБ (гейт +3.0) → {verdict}"
    )
    print(f"  проекция фона на оценку речи: {quality['bg_speech_projection_db']:+.2f} дБ")
    print(f"  α вычитания: {quality['alpha']}")
    if "stem_rms_db" in quality:
        print(f"  RMS по стемам: {quality['stem_rms_db']}")
    print(
        f"  RMS микса {quality['mix_rms_db']:+.2f} / речи {quality['speech_rms_db']:+.2f} "
        f"/ фона {quality['background_rms_db']:+.2f} дБ"
    )
    if truth is not None:
        projection, ratio = _truth_rejection(mix, result.background, truth)
        print(
            f"  ИСТИННАЯ утечка речи в фон (эталон): проекция {projection:+.2f} дБ, "
            f"по энергии {ratio:+.2f} дБ"
        )


def _truth_rejection(mix: Audio, background: Audio, truth: Audio) -> tuple[float, float]:
    """Настоящая утечка: проекция фона на эталонную речь в окнах, где речь реально есть.

    Метрика не зависит от бэкенда и не вырождается: эталон известен из сборки тестовой
    дорожки, поэтому видно и «призрак» от вычитания, и остатки в стемах.
    """
    truth_mono = truth.mono().samples.astype(np.float64)
    bg_mono = background.mono().samples.astype(np.float64)
    rate = truth.sample_rate
    hop = rate // 20
    frames = len(truth_mono) // hop
    active = np.zeros(frames, dtype=bool)
    for index in range(frames):
        frame = truth_mono[index * hop : (index + 1) * hop]
        active[index] = 20 * np.log10(max(float(np.sqrt(np.mean(frame**2))), 1e-8)) > -45.0

    n = min(len(truth_mono), len(bg_mono), frames * hop, len(truth_mono))
    mask = np.repeat(active, hop)[:n].astype(bool)
    if not mask.any():
        return -120.0, -120.0
    reference = truth_mono[:n][mask]
    estimated = bg_mono[:n][mask]
    denominator = float(np.dot(reference, reference))
    if denominator <= 1e-12:
        return -120.0, -120.0
    coefficient = float(np.dot(estimated, reference) / denominator)
    projection_db = 20 * np.log10(max(abs(coefficient), 1e-6))
    rms_bg = float(np.sqrt(np.mean(np.square(estimated))))
    rms_truth = float(np.sqrt(np.mean(np.square(reference))))
    ratio_db = 20 * np.log10(max(rms_bg, 1e-9) / max(rms_truth, 1e-9))
    return float(projection_db), float(ratio_db)


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    compare = "--compare" in sys.argv
    path = Path(args[0]) if args else _find_mix()

    truth: Audio | None = None
    if "--truth" in sys.argv:
        truth_path = Path(sys.argv[sys.argv.index("--truth") + 1])
        truth = read(truth_path)
        print(f"эталон речи: {truth_path} ({truth.duration_s:.2f} с, {truth.sample_rate} Гц)")

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
        _report(
            "Bandit v2 (music+sfx, без вычитания)",
            mix,
            result,
            time.perf_counter() - started,
            truth,
        )
    else:
        started = time.perf_counter()
        speech = separation.BACKENDS["demucs"](mix)
        result = separation.subtract(
            mix, speech, mode=settings.separation_mode, ducking_db=settings.default_ducking_db
        )
        _report(
            "Demucs (вычитание + дуккинг)", mix, result, time.perf_counter() - started, truth
        )

    if compare and primary != "demucs":
        started = time.perf_counter()
        speech = separation.BACKENDS["demucs"](mix)
        result = separation.subtract(
            mix, speech, mode=settings.separation_mode, ducking_db=settings.default_ducking_db
        )
        _report(
            "Demucs (вычитание + дуккинг)", mix, result, time.perf_counter() - started, truth
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
