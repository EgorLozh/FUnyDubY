"""Отделение речи и формирование фона (решение D1/D2 архитектуры).

Ключевая идея (§1, §16.2): фон строится **вычитанием** оценки речи из микса,
а не суммой стемов, чтобы не потерять музыку, SFX и всё, что модель ошибочно
отнесла к речи:

    background = mix − α · speech_est

α подбирается методом наименьших квадратов по окнам активной речи — без этого
вычитание либо оставляет «призрака» голоса, либо вырезает из фона лишнее.
Поверх фона применяется дуккинг в зонах речи, который маскирует остаток.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.ml.audio_io import Audio, optimal_scale, peak_db, read, rms_db, to_stereo, write_float32

log = get_logger("ml.separation")


class SeparationFailed(DomainError):
    status_code = 500
    code = "separation_failed"


class NoSpeechFound(DomainError):
    status_code = 422
    code = "no_speech_found"


@dataclass
class SeparationResult:
    speech: Audio
    background: Audio
    alpha: float
    mode: str
    backend: str
    quality: dict


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


# ------------------------------------------------------------------ backend: demucs


def _demucs_speech_estimate(mix: Audio) -> Audio:
    """Оценка речи через Demucs v4 (стем vocals). Fallback-бэкенд D2."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model("htdemucs")
    model.eval()
    device = torch.device(_device())
    model.to(device)

    stereo = to_stereo(mix)
    tensor = torch.from_numpy(np.ascontiguousarray(stereo.samples.T)).float()
    with torch.no_grad():
        sources = apply_model(
            model,
            tensor[None],
            device=device,
            shifts=1,
            split=True,
            overlap=0.25,
            progress=False,
        )[0]

    index = model.sources.index("vocals") if "vocals" in model.sources else 0
    vocals = sources[index].cpu().numpy().T
    del sources
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return Audio(vocals.astype(np.float32), stereo.sample_rate)


# ------------------------------------------------------------- backend: bandit v2


def _bandit_speech_estimate(mix: Audio) -> Audio:
    """Оценка речи через Bandit v2 (speech/music/effects) — основной бэкенд D1.

    Требует код репозитория и веса в MODEL_CACHE_DIR/bandit_v2: research-код автора
    не публикуется в PyPI, поэтому импорт/веса проверяются явно и с понятной ошибкой.
    """
    weights_dir = Path(settings.model_cache_dir) / "bandit_v2"
    if not weights_dir.exists():
        raise SeparationFailed(
            f"Нет весов Bandit v2 в {weights_dir}. Скачайте их (Zenodo record 12701995) "
            f"или переключитесь на SEPARATION_MODEL=demucs",
            code="bandit_weights_missing",
        )
    try:
        from bandit.model import Bandit  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SeparationFailed(
            "Код Bandit v2 не установлен в образ воркера (см. requirements-ml.txt / vendor/)",
            code="bandit_code_missing",
        ) from exc

    raise SeparationFailed(
        "Загрузка весов Bandit v2 ещё не подключена — используйте SEPARATION_MODEL=demucs",
        code="bandit_not_wired",
    )


BACKENDS = {"demucs": _demucs_speech_estimate, "bandit_v2": _bandit_speech_estimate}


# ------------------------------------------------------------------- постобработка


def speech_activity_mask(mix: Audio, speech: Audio, hop_ms: int = 50, threshold_db: float = -45.0) -> np.ndarray:
    """Окна, где в оценке речи есть энергия — по ним подбирается α и делается дуккинг."""
    mono_speech = speech.mono().samples
    hop = max(1, int(speech.sample_rate * hop_ms / 1000))
    frames = max(1, len(mono_speech) // hop)
    mask = np.zeros(frames, dtype=bool)
    for index in range(frames):
        frame = mono_speech[index * hop : (index + 1) * hop]
        if frame.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float64)))))
        mask[index] = 20 * np.log10(max(rms, 1e-8)) > threshold_db
    if not mask.any():
        raise NoSpeechFound("В дорожке не найдено речи — фон остаётся без изменений")
    return mask


def expand_mask_to_samples(mask: np.ndarray, hop: int, total: int) -> np.ndarray:
    return np.repeat(mask, hop)[:total]


def duck_background(background: Audio, speech_mask: np.ndarray, ducking_db: float) -> Audio:
    """Приглушить фон в зонах речи с плавными фронтами (атака 20 мс, релиз 120 мс)."""
    if ducking_db >= 0:
        return background
    samples = background.samples.astype(np.float32).copy()
    gain = np.where(speech_mask, 10 ** (ducking_db / 20), 1.0).astype(np.float32)

    rate = background.sample_rate
    attack = max(1, int(rate * 0.02))
    release = max(1, int(rate * 0.12))
    smoothed = gain.copy()
    for index in range(1, len(gain)):
        target = gain[index]
        coefficient = attack if target < smoothed[index - 1] else release
        step = 1.0 / coefficient
        smoothed[index] = smoothed[index - 1] + (target - smoothed[index - 1]) * step

    if samples.ndim == 2:
        smoothed = smoothed[:, None]
    return Audio((samples * smoothed).astype(np.float32), rate)


def subtract(
    mix: Audio,
    speech: Audio,
    *,
    mode: str,
    ducking_db: float,
) -> SeparationResult:
    """Собрать фон из микса и оценки речи по выбранной политике."""
    n = min(len(mix.samples), len(speech.samples))
    mix_cut = Audio(mix.samples[:n], mix.sample_rate)
    speech_cut = Audio(speech.samples[:n], speech.sample_rate)

    hop = max(1, int(speech_cut.sample_rate * 0.05))
    mask_frames = speech_activity_mask(mix_cut, speech_cut, hop_ms=50)
    sample_mask = expand_mask_to_samples(mask_frames, hop, n)

    alpha = optimal_scale(mix_cut, speech_cut, mask=sample_mask)
    if mode == "stems_sum":
        # Для A/B: фон = микс минус речь без масштабирования (то же, но α=1)
        alpha = 1.0

    mix_samples = mix_cut.samples.astype(np.float32)
    speech_samples = speech_cut.samples.astype(np.float32)
    if mix_samples.ndim == 1 and speech_samples.ndim == 2:
        mix_samples = np.stack([mix_samples, mix_samples], axis=1)
    if speech_samples.ndim == 1 and mix_samples.ndim == 2:
        speech_samples = np.stack([speech_samples, speech_samples], axis=1)

    background = mix_samples - alpha * speech_samples
    background = np.clip(background, -1.0, 1.0)

    background_audio = Audio(background, mix_cut.sample_rate)
    leak_db = _residual_leak_db(mix_cut, background_audio, sample_mask)

    if mode in ("subtract_and_duck", "stems_sum"):
        background_audio = duck_background(background_audio, sample_mask, ducking_db)

    quality = {
        "residual_speech_db": round(leak_db, 2),
        "alpha": round(alpha, 3),
        "mix_rms_db": round(rms_db(mix_cut), 2),
        "background_rms_db": round(rms_db(background_audio), 2),
        "background_peak_db": round(peak_db(background_audio), 2),
        "speech_rms_db": round(rms_db(speech_cut), 2),
    }
    if leak_db > -15.0:
        quality["degraded"] = True
        log.warning("separation_degraded", residual_speech_db=round(leak_db, 2), alpha=alpha)
    return SeparationResult(
        speech=speech_cut,
        background=background_audio,
        alpha=alpha,
        mode=mode,
        backend=settings.separation_model,
        quality=quality,
    )


def _residual_leak_db(mix: Audio, background: Audio, mask: np.ndarray) -> float:
    """Метрика «призрака»: сколько энергии микса осталось в фоне в зонах речи.

    Сравниваем энергию фона в речевых окнах с энергией самого микса там же:
    чем сильнее подавлено, тем ближе это значение к ‑∞ (на практике — к ‑20…-40 дБ).
    """
    mix_samples = mix.mono().samples[: len(mask)]
    bg_samples = background.mono().samples[: len(mask)]
    if not mask.any():
        return -120.0
    mix_energy = float(np.sum(np.square(mix_samples[mask].astype(np.float64))))
    bg_energy = float(np.sum(np.square(bg_samples[mask].astype(np.float64))))
    if mix_energy <= 1e-12:
        return -120.0
    ratio = max(bg_energy, 1e-20) / mix_energy
    return float(10 * np.log10(max(ratio, 1e-12)))


def separate_file(
    mix_path: Path,
    speech_out: Path,
    background_out: Path,
    *,
    mode: str | None = None,
    ducking_db: float | None = None,
    fallback: bool = True,
) -> SeparationResult:
    """Прочитать микс, отделить речь, записать две дорожки (float32 WAV)."""
    mode = mode or settings.separation_mode
    ducking_db = settings.default_ducking_db if ducking_db is None else ducking_db

    mix = read(mix_path)
    backend_name = settings.separation_model
    try:
        speech = BACKENDS[backend_name](mix)
    except Exception as exc:  # noqa: BLE001
        if not fallback or backend_name == "demucs":
            raise
        log.warning("separation_backend_failed", backend=backend_name, error=str(exc))
        speech = BACKENDS["demucs"](mix)
        backend_name = "demucs"

    result = subtract(mix, speech, mode=mode, ducking_db=ducking_db)
    result.backend = backend_name
    write_float32(speech_out, result.speech)
    write_float32(background_out, result.background)
    log.info("separation_done", backend=backend_name, mode=mode, **result.quality)
    return result


def ensure_stereo_speech(speech_path: Path, out_path: Path) -> None:
    """Речь в стерео — нужна для единообразия нарезки и микширования."""
    audio = read(speech_path)
    write_float32(out_path, to_stereo(audio))
