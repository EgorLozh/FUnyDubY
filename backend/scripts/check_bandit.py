"""Проверка Bandit v2 в контейнере воркера: загрузка весов, разделение, метрики.

Запуск (внутри worker-gpu):
    python scripts/check_bandit.py [путь_к_wav]

Без аргумента берётся первый найденный микс в /data/rooms/*/audio/.
Печатает длительности, RMS по стемам и остаток речи в фоне — ту самую метрику,
по которой в ADR зафиксирован порог пересмотра решения D1 (‑15 дБ).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.ml import bandit
from app.ml.audio_io import read, rms_db


def _find_mix() -> Path:
    for pattern in ("mix*", "original*", "*.wav"):
        for path in sorted(Path("/data/rooms").glob(f"*/audio/{pattern}")):
            if path.is_file():
                return path
    raise SystemExit("Не найден тестовый микс в /data/rooms/*/audio/")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_mix()
    print(f"файл: {path}")
    mix = read(path)
    print(f"микс: {mix.duration_s:.2f} с, {mix.sample_rate} Гц, каналов {mix.channels}")

    started = time.perf_counter()
    stems = bandit.separate(mix, weights_path=settings.bandit_weights_path)
    elapsed = time.perf_counter() - started

    print(f"разделение: {elapsed:.1f} с ({bandit.chunk_count(mix.duration_s)} чанков)")
    for name, audio in sorted(stems.items()):
        print(f"  стем {name:<8} {audio.duration_s:.2f} с, RMS {rms_db(audio):6.2f} дБ")

    speech = stems.get("speech")
    if speech is None:
        print("ОШИБКА: нет стема speech")
        return 1

    background = bandit.sum_stems(stems, ("music", "sfx"), mix.sample_rate)
    mono_speech = speech.mono().samples.astype(np.float64)
    hop = mix.sample_rate // 20
    frames = len(mono_speech) // hop
    mask = np.zeros(frames, dtype=bool)
    for index in range(frames):
        frame = mono_speech[index * hop : (index + 1) * hop]
        mask[index] = 20 * np.log10(max(float(np.sqrt(np.mean(frame**2))), 1e-8)) > -45.0
    coverage = float(mask.mean()) * 100 if frames else 0.0
    print(f"доля окон с речью: {coverage:.1f}% (ожидаемо есть заметная доля при диалоге)")

    mix_mono = mix.mono().samples.astype(np.float64)
    bg_mono = background.mono().samples.astype(np.float64)
    n = min(len(mix_mono), len(bg_mono), frames * hop)
    sample_mask = np.repeat(mask, hop)[:n]
    mix_energy = float(np.sum(mix_mono[:n][sample_mask] ** 2))
    bg_energy = float(np.sum(bg_mono[:n][sample_mask] ** 2))
    leak = 10 * np.log10(max(bg_energy, 1e-20) / max(mix_energy, 1e-20))
    print(f"остаток речи в фоне (music+sfx) в зонах речи: {leak:.2f} дБ")
    print("порог D1: ‑15 дБ; Demucs на этом же клипе давал ‑15.28 дБ (до дуккинга)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
