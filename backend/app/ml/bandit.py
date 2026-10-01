"""Инференс Bandit v2 — основного бэкенда разделения речи (решение D1 архитектуры).

Bandit обучен на диалогах кино (датасет DnR) и раздаёт три стема: **speech**, **music**, **sfx**.
Это даёт принципиально другую схему сборки фона, чем fallback на Demucs: фон = music + sfx
(сумма стемов), а не `mix − α·speech`. Так музыка и эффекты сохраняются без потерь, которые
неизбежны при вычитании.

Вендоренный код: `app/ml/vendor/bandit_v2` (Apache-2.0, upstream kwatcharasupat/bandit-v2,
commit d5563d9031e95fdaa3e5a73d5020b9a0df61adb6). Веса: Zenodo record 12701995, CC-BY-SA-4.0.

Модель моно и работает на 48 кГц. Обработка — чанками 8 с с шагом 1 с и перекрёстным
затуханием на стыках (как в авторском `configs/inference/chunked-tensor.yaml`): целиком
10-минутный ролик не поместится в VRAM из-за рекуррентного блока по 64 полосам.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.ml.audio_io import Audio

log = get_logger("ml.bandit")

BANDIT_FS = 48000
CHUNK_SECONDS = 8.0
HOP_SECONDS = 1.0

# Из configs/models/bandit-mus64.yaml репозитория автора (проверено на чекпоинте multi:
# 2952 тензора, стемы speech/music/sfx).
MODEL_KWARGS: dict[str, object] = {
    "in_channels": 1,
    "band_type": "musical",
    "n_bands": 64,
    "normalize_channel_independently": False,
    "treat_channel_as_feature": True,
    "n_sqm_modules": 8,
    "emb_dim": 128,
    "rnn_dim": 256,
    "bidirectional": True,
    "rnn_type": "GRU",
    "mlp_dim": 512,
    "hidden_activation": "Tanh",
    "hidden_activation_kwargs": None,
    "complex_mask": True,
    "use_freq_weights": True,
    "n_fft": 2048,
    "win_length": 2048,
    "hop_length": 512,
    "window_fn": "hann_window",
    "wkwargs": None,
    "power": None,
    "center": True,
    "normalized": True,
    "pad_mode": "reflect",
    "onesided": True,
}

STEM_ORDER = ("speech", "music", "sfx")

# Модель держим в кеше процесса: загрузка чекпоинта 447 МБ заметно дороже самого инференса.
_CACHE: dict[tuple[str, str], object] = {}


class BanditUnavailable(DomainError):
    status_code = 500
    code = "bandit_weights_missing"


def _device_name() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def stems_from_state_dict(state: dict) -> list[str]:
    """Имена стемов берём из чекпоинта — это надёжнее, чем хардкод порядка."""
    found = {
        key.split(".")[2]
        for key in state
        if key.startswith("model.mask_estim.") and key.count(".") >= 3
    }
    ordered = [stem for stem in STEM_ORDER if stem in found]
    ordered += sorted(found.difference(STEM_ORDER))
    return ordered or list(STEM_ORDER)


def load_model(weights_path: str | Path, *, device: str | None = None) -> object:
    """Загрузить Bandit из чекпоинта Lightning (веса лежат под префиксом `model.`)."""
    import torch

    from app.ml.vendor.bandit_v2.bandit.bandit import Bandit

    path = Path(weights_path)
    if not path.exists():
        raise BanditUnavailable(
            f"Нет файла весов Bandit: {path}. Скачайте чекпоинт (Zenodo record 12701995, "
            f"checkpoint-multi.ckpt) в этот путь или переключитесь на SEPARATION_MODEL=demucs",
        )

    device = device or _device_name()
    cache_key = (str(path), device)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    state = checkpoint.get("state_dict") or {}
    stems = stems_from_state_dict(state)
    model = Bandit(fs=BANDIT_FS, stems=stems, **MODEL_KWARGS)

    weights = {
        key[len("model.") :]: value for key, value in state.items() if key.startswith("model.")
    }
    missing, unexpected = model.load_state_dict(weights, strict=False)
    if missing:
        raise BanditUnavailable(
            f"В чекпоинте не хватает {len(missing)} тензоров модели (например {missing[:3]}) — "
            f"архитектура в MODEL_KWARGS не совпадает с обученной",
        )
    if unexpected:
        # loss_handler.* и прочие вспомогательные веса обучения нас не касаются
        log.info("bandit_unexpected_keys", count=len(unexpected))

    model.eval()
    model.to(device)
    _CACHE[cache_key] = model
    log.info("bandit_loaded", weights=str(path), device=device, stems=stems)
    return model


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    try:
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(int(src_rate), int(dst_rate))
        resampled = resample_poly(samples, int(dst_rate) // divisor, int(src_rate) // divisor)
        return resampled.astype(np.float32)
    except ImportError:  # pragma: no cover - scipy есть в ML-образе (зависимость pyannote)
        count = int(round(len(samples) * dst_rate / src_rate))
        positions = np.linspace(0, len(samples), count, endpoint=False)
        return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def _window(size: int, ramp: int) -> np.ndarray:
    """Трапеция: линейные фронты длиной ramp по краям (перекрёстное затухание на стыках)."""
    window = np.ones(size, dtype=np.float64)
    ramp = max(1, min(ramp, size // 2))
    edge = np.linspace(0.0, 1.0, ramp, endpoint=False)
    window[:ramp] = edge
    window[size - ramp :] = edge[::-1]
    return window


def separate(mix: Audio, *, weights_path: str | Path, device: str | None = None) -> dict[str, Audio]:
    """Разделить микс на стемы. Возвращает {имя_стема: Audio} (моно, 48 кГц)."""
    import torch

    model = load_model(weights_path, device=device)
    device = device or _device_name()
    stems = list(getattr(model, "stems", STEM_ORDER))

    mono = mix.mono().samples.astype(np.float32)
    mono = _resample(mono, mix.sample_rate, BANDIT_FS)

    total = len(mono)
    chunk = int(CHUNK_SECONDS * BANDIT_FS)
    hop = int(HOP_SECONDS * BANDIT_FS)
    if total == 0:
        return {stem: Audio(np.zeros(0, dtype=np.float32), BANDIT_FS) for stem in stems}

    start = 0
    starts: list[int] = []
    while start + chunk <= total:
        starts.append(start)
        start += hop
    if not starts or starts[-1] + chunk < total:
        starts.append(max(0, total - chunk))

    window = _window(chunk, hop)
    accumulator = {stem: np.zeros(total, dtype=np.float64) for stem in stems}
    weight = np.zeros(total, dtype=np.float64)

    for index, offset in enumerate(starts):
        segment = mono[offset : offset + chunk]
        valid = len(segment)
        if valid < chunk:
            segment = np.pad(segment, (0, chunk - valid))
        tensor = torch.from_numpy(np.ascontiguousarray(segment)).view(1, 1, chunk).to(device)
        with torch.inference_mode():
            output = model({"mixture": {"audio": tensor}})
        for stem in stems:
            estimate = output["estimates"][stem]["audio"][0, 0, :valid]
            accumulator[stem][offset : offset + valid] += (
                estimate.detach().cpu().numpy().astype(np.float64) * window[:valid]
            )
        weight[offset : offset + valid] += window[:valid]
        del output, tensor
        if index % 8 == 7:
            log.debug("bandit_chunk", done=index + 1, total=len(starts))

    if device == "cuda":  # noqa: SIM108 - освобождаем кеш только когда работали на GPU
        torch.cuda.empty_cache()

    weight = np.maximum(weight, 1e-6)
    result: dict[str, Audio] = {}
    for stem in stems:
        samples = (accumulator[stem] / weight).astype(np.float32)
        result[stem] = Audio(np.clip(samples, -1.0, 1.0), BANDIT_FS)
    log.info("bandit_separated", seconds=round(total / BANDIT_FS, 2), chunks=len(starts))
    return result


def sum_stems(stems: dict[str, Audio], names: tuple[str, ...], sample_rate: int) -> Audio:
    """Сложить стемы (например music + sfx) в одну дорожку нужной длины."""
    parts = [stems[name].samples.astype(np.float32) for name in names if name in stems]
    if not parts:
        return Audio(np.zeros(0, dtype=np.float32), sample_rate)
    length = min(len(part) for part in parts)
    total = np.sum([part[:length] for part in parts], axis=0)
    return Audio(np.clip(total, -1.0, 1.0), sample_rate)


def resample_audio(audio: Audio, rate: int) -> Audio:
    """Привести дорожку к другой частоте дискретизации (Bandit работает на 48 кГц)."""
    if audio.sample_rate == rate or len(audio.samples) == 0:
        return audio
    data = audio.samples
    if data.ndim == 1:
        return Audio(_resample(data, audio.sample_rate, rate), rate)
    channels = [_resample(data[:, index], audio.sample_rate, rate) for index in range(data.shape[1])]
    length = min(len(channel) for channel in channels)
    stacked = np.stack([channel[:length] for channel in channels], axis=1)
    return Audio(stacked.astype(np.float32), rate)


def chunk_count(duration_s: float) -> int:
    """Сколько чанков будет обработано — для оценки прогресса этапа."""
    chunk = CHUNK_SECONDS
    hop = HOP_SECONDS
    if duration_s <= chunk:
        return 1
    return int(math.ceil((duration_s - chunk) / hop)) + 1
