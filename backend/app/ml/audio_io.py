"""Чтение/запись аудио для ML-этапов и микширования (soundfile + numpy)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass
class Audio:
    samples: np.ndarray  # float32, форма (n,) для моно или (n, channels)
    sample_rate: int

    @property
    def channels(self) -> int:
        return 1 if self.samples.ndim == 1 else self.samples.shape[1]

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate

    @property
    def duration_ms(self) -> int:
        return int(round(self.duration_s * 1000))

    def mono(self) -> "Audio":
        if self.samples.ndim == 1:
            return self
        return Audio(self.samples.mean(axis=1), self.sample_rate)

    def segment(self, start_ms: int, end_ms: int) -> "Audio":
        start = max(0, int(self.sample_rate * start_ms / 1000))
        end = min(len(self.samples), int(self.sample_rate * end_ms / 1000))
        return Audio(self.samples[start:end], self.sample_rate)


def read(path: Path, mono: bool = False) -> Audio:
    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    audio = Audio(data, rate)
    return audio.mono() if mono else audio


def write(path: Path, audio: Audio) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio.samples, audio.sample_rate, subtype="PCM_16")


def write_float32(path: Path, audio: Audio) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio.samples, audio.sample_rate, subtype="FLOAT")


def to_stereo(audio: Audio) -> Audio:
    if audio.samples.ndim == 2 and audio.samples.shape[1] == 2:
        return audio
    mono = audio.mono().samples
    return Audio(np.stack([mono, mono], axis=1), audio.sample_rate)


def rms_db(audio: Audio) -> float:
    """Уровень RMS в дБFS — используется для контроля качества разделения и тишины тейков."""
    samples = audio.samples
    if samples.size == 0:
        return -120.0
    value = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
    return 20 * np.log10(max(value, 1e-6))


def peak_db(audio: Audio) -> float:
    if audio.samples.size == 0:
        return -120.0
    return 20 * np.log10(max(float(np.max(np.abs(audio.samples))), 1e-6))


def optimal_scale(reference: Audio, target: Audio, mask: np.ndarray | None = None) -> float:
    """Оптимальный коэффициент α для `reference − α·target` (метод наименьших квадратов).

    Используется в вычитании речи: без него недобор усиления оставляет «призрака» голоса,
    а перебор — вырезает из фона лишнее.
    """
    ref = reference.samples.astype(np.float64)
    tgt = target.samples.astype(np.float64)
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    if tgt.ndim > 1:
        tgt = tgt.mean(axis=1)
    n = min(len(ref), len(tgt))
    ref, tgt = ref[:n], tgt[:n]
    if mask is not None:
        mask = mask[:n].astype(bool)
        if not mask.any():
            mask = np.ones(n, dtype=bool)
        ref, tgt = ref[mask], tgt[mask]
    denom = float(np.dot(tgt, tgt))
    if denom <= 1e-12:
        return 1.0
    alpha = float(np.dot(ref, tgt) / denom)
    return float(np.clip(alpha, 0.5, 2.0))
