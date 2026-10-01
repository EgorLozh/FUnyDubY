"""Финальный микс: фон комнаты + записанные тейки, разложенные по таймкодам реплик.

Микшируем в numpy (PCM float32): ffmpeg остаётся только на мультиплекс с видео, поэтому
пересборка не перекодирует картинку и не зависит от фильтров.

Правила:

* основа — `speech/background.wav` (музыка и эффекты; речь из него уже вычтена, а в зонах
  речи фон подсажен дуккингом на этапе разделения);
* на место каждой озвученной реплики кладётся **актуальный тейк** длиной ровно в реплику;
* реплика с `keep_original` или неозвученная при `unrecorded_policy="original"` получает
  свою нарезку оригинальной речи;
* края вставок подрезаются коротким фейдом (щелчки на склейке), перекрывающиеся реплики
  складываются с плавным перекрёстным затуханием;
* если сумма упёрлась в потолок, масштабируем весь микс, а не режем пики — иначе тембр
  записанных голосов поедет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.ml.audio_io import Audio, peak_db, read, to_stereo

FADE_MS = 12
CEILING_DB = -1.0


@dataclass
class MixSlot:
    """Одна вставка в микс: что и куда кладём."""

    line_id: str
    start_ms: int
    end_ms: int
    source: Path | None  # None — реплика остаётся без вставки (только фон)
    kind: str = "take"  # take | original | silent


@dataclass
class MixResult:
    audio: Audio
    used_takes: int = 0
    used_originals: int = 0
    silent_lines: int = 0
    applied_gain_db: float = 0.0
    peak_db: float = 0.0
    slot_problems: list[str] = field(default_factory=list)


def _fade(samples: np.ndarray, fade: int) -> np.ndarray:
    """Короткое линейное нарастание/затухание по краям вставки: убирает щелчок на склейке."""
    if fade <= 0 or len(samples) <= 2 * fade:
        return samples
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    samples[:fade] *= ramp
    samples[-fade:] *= ramp[::-1]
    return samples


def build_mix(
    background: Audio,
    slots: list[MixSlot],
    *,
    sample_rate: int | None = None,
    fade_ms: int = FADE_MS,
    ceiling_db: float = CEILING_DB,
) -> MixResult:
    """Наложить вставки на фон и вернуть готовый микс."""
    rate = sample_rate or background.sample_rate
    base = to_stereo(background)
    if base.sample_rate != rate:  # фон всегда 48 кГц; страхуемся от рассинхрона
        raise ValueError(f"Частота фона {base.sample_rate} не совпадает с {rate}")

    mix = base.samples.astype(np.float32, copy=True)
    if mix.ndim == 1:
        mix = np.stack([mix, mix], axis=1)
    total = len(mix)
    fade = int(rate * fade_ms / 1000)

    result = MixResult(audio=Audio(mix, rate))
    for slot in sorted(slots, key=lambda s: s.start_ms):
        if slot.source is None:
            result.silent_lines += 1
            continue

        try:
            clip = read(slot.source, mono=True).samples.astype(np.float32)
        except Exception as exc:  # битый/пропавший файл не должен ронять весь рендер
            result.slot_problems.append(f"{slot.kind}:{slot.line_id}: {exc}")
            result.silent_lines += 1
            continue

        clip = _fade(clip, fade)
        start = max(0, int(rate * slot.start_ms / 1000))
        if start >= total:
            result.slot_problems.append(f"{slot.kind}:{slot.line_id}: начало за пределами видео")
            result.silent_lines += 1
            continue
        length = min(len(clip), total - start)
        mix[start : start + length] += np.stack([clip[:length], clip[:length]], axis=1)
        if slot.kind == "take":
            result.used_takes += 1
        else:
            result.used_originals += 1

    peak = peak_db(Audio(mix, rate))
    if peak > ceiling_db:
        gain = 10 ** ((ceiling_db - peak) / 20)
        mix *= gain
        result.applied_gain_db = round(-(peak - ceiling_db), 2)
        peak = ceiling_db
    result.peak_db = round(float(peak), 2)
    result.audio = Audio(mix, rate)
    return result
