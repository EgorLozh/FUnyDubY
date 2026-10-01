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
    alpha: float | None  # None для пути «сумма стемов»: вычитания там нет
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


def _bandit_stems(mix: Audio) -> dict[str, Audio]:
    """Стемы Bandit v2 (speech/music/sfx) — основной бэкенд D1."""
    from app.ml import bandit

    return bandit.separate(mix, weights_path=settings.bandit_weights_path)


BACKENDS = {"demucs": _demucs_speech_estimate}


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
    """Развернуть оконную маску в пошаговую. Длину добираем до total, иначе массивы разъедутся."""
    expanded = np.repeat(mask, hop)
    if len(expanded) >= total:
        return expanded[:total]
    return np.concatenate([expanded, np.zeros(total - len(expanded), dtype=expanded.dtype)])


def duck_background(background: Audio, speech_mask: np.ndarray, ducking_db: float) -> Audio:
    """Приглушить фон в зонах речи с плавными фронтами (атака 20 мс, релиз 120 мс).

    Огибающая считается на сетке 1 мс и растягивается интерполяцией: поэлементный цикл
    по 20 млн сэмплов (10-минутный ролик) занял бы десятки секунд, а точность 1 мс
    для дуккинга избыточна.
    """
    if ducking_db >= 0:
        return background

    samples = background.samples.astype(np.float32)
    rate = background.sample_rate

    if len(speech_mask) != len(samples):
        speech_mask = np.resize(speech_mask, len(samples))

    step = max(1, int(rate / 1000))  # 1 мс
    frames = len(samples) // step
    if frames == 0:
        return background

    coarse_target = np.where(
        speech_mask[: frames * step].reshape(frames, step).any(axis=1),
        10 ** (ducking_db / 20),
        1.0,
    ).astype(np.float64)

    attack = max(1, int(0.020 * 1000))  # в шагах сетки
    release = max(1, int(0.120 * 1000))
    envelope = np.empty(frames, dtype=np.float64)
    envelope[0] = coarse_target[0]
    for index in range(1, frames):
        target = coarse_target[index]
        coefficient = attack if target < envelope[index - 1] else release
        envelope[index] = envelope[index - 1] + (target - envelope[index - 1]) / coefficient

    positions = np.arange(len(samples), dtype=np.float64)
    full = np.interp(positions, np.arange(frames, dtype=np.float64) * step, envelope)
    gain = full.astype(np.float32)
    if samples.ndim == 2:
        gain = gain[:, None]
    return Audio((samples * gain).astype(np.float32), rate)


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
    leak_db, duck_db = speech_leak_db(mix_cut, speech_cut, background_audio, sample_mask)

    if mode in ("subtract_and_duck", "stems_sum"):
        background_audio = duck_background(background_audio, sample_mask, ducking_db)

    quality = {
        "speech_window_gain_db": round(duck_db, 2),
        "bg_speech_projection_db": round(leak_db, 2),
        "alpha": round(alpha, 3),
        "mix_rms_db": round(rms_db(mix_cut), 2),
        "background_rms_db": round(rms_db(background_audio), 2),
        "background_peak_db": round(peak_db(background_audio), 2),
        "speech_rms_db": round(rms_db(speech_cut), 2),
        "ducking_db": ducking_db,
    }
    # Гейт качества: если фон в зонах речи громче, чем вне их, значит в него просочился голос.
    # Проекционная метрика для пути вычитания вырождена (фон ортогонализован к оценке речи),
    # поэтому решение о деградации принимается по уровню, а истинная утечка меряется
    # приёмочным тестом на эталонной дорожке (scripts/check_separation.py --truth).
    if duck_db > 3.0:
        quality["degraded"] = True
        log.warning("separation_degraded", speech_window_gain_db=round(duck_db, 2), alpha=alpha)
    return SeparationResult(
        speech=speech_cut,
        background=background_audio,
        alpha=alpha,
        mode=mode,
        backend=settings.separation_model,
        quality=quality,
    )


def speech_leak_db(mix: Audio, speech: Audio, background: Audio, mask: np.ndarray) -> float:
    """Сколько самой речи осталось в фоне — проекция фона на оценку речи.

    Прежняя версия метрики делила энергию фона на энергию микса в зонах речи и потому
    измеряла громкость музыки, а не утечку голоса: громкий саундтрек давал «плохое»
    значение даже у идеального разделения. Здесь считается корреляционная проекция:

        α = <фон, речь> / <речь, речь>   →   20·log10|α|

    Если речи в фоне нет, α близко к нулю (десятки отрицательных дБ); если фон содержит
    «призрак» голоса — α приближается к 0 дБ. Рядом возвращается `bg_duck_db` — насколько
    фон в зонах речи тише, чем вне их (нужно для решения о дуккинге).
    """
    mix_samples = mix.mono().samples.astype(np.float64)
    speech_samples = speech.mono().samples.astype(np.float64)
    bg_samples = background.mono().samples.astype(np.float64)
    n = min(len(mix_samples), len(speech_samples), len(bg_samples), len(mask))
    if n == 0:
        return -120.0, 0.0

    window = mask[:n].astype(bool)
    if not window.any():
        return -120.0, 0.0

    speech_window = speech_samples[:n][window]
    background_window = bg_samples[:n][window]
    denominator = float(np.dot(speech_window, speech_window))
    if denominator <= 1e-12:
        return -120.0, 0.0

    alpha = float(np.dot(background_window, speech_window) / denominator)
    leak = 20 * np.log10(max(abs(alpha), 1e-6))

    outside = ~window
    if outside.any():
        inside_rms = float(np.sqrt(np.mean(np.square(background_window))))
        outside_rms = float(np.sqrt(np.mean(np.square(bg_samples[:n][outside]))))
        duck = 20 * np.log10(max(inside_rms, 1e-9) / max(outside_rms, 1e-9))
    else:
        duck = 0.0
    return float(leak), float(duck)


def _speech_leak_only(mix: Audio, speech: Audio, background: Audio, mask: np.ndarray) -> float:
    """Удобная обёртка, когда нужна только утечка."""
    return speech_leak_db(mix, speech, background, mask)[0]


def from_stems(mix: Audio, stems: dict[str, Audio], *, ducking_db: float) -> SeparationResult:
    """Собрать результат из стемов Bandit: фон = music + sfx.

    Здесь принципиально нет вычитания: модель сама отделяет речь, поэтому музыка и эффекты
    сохраняются без потерь. Дуккинг по умолчанию выключен (`BANDIT_DUCKING_DB=0`) — он был
    компенсацией «призрака» при вычитании, а не художественным приёмом.
    """
    from app.ml import bandit as bandit_mod

    speech = stems.get("speech")
    if speech is None:
        raise SeparationFailed("Bandit не вернул стем речи", code="bandit_no_speech_stem")

    background = bandit_mod.sum_stems(stems, ("music", "sfx"), mix.sample_rate)
    if speech.sample_rate != mix.sample_rate:
        speech = bandit_mod.resample_audio(speech, mix.sample_rate)
        background = bandit_mod.resample_audio(background, mix.sample_rate)

    speech = to_stereo(speech)
    background = to_stereo(background)
    n = min(len(mix.samples), len(speech.samples), len(background.samples))
    if n == 0:
        raise SeparationFailed("Пустой микс или пустые стемы", code="bandit_empty_audio")

    mix_cut = Audio(mix.samples[:n], mix.sample_rate)
    speech_cut = Audio(speech.samples[:n], mix.sample_rate)
    background_cut = Audio(background.samples[:n], mix.sample_rate)

    hop = max(1, int(mix_cut.sample_rate * 0.05))
    mask_frames = speech_activity_mask(mix_cut, speech_cut, hop_ms=50)
    sample_mask = expand_mask_to_samples(mask_frames, hop, n)

    leak_db, duck_db = speech_leak_db(mix_cut, speech_cut, background_cut, sample_mask)
    quality = {
        "speech_window_gain_db": round(duck_db, 2),
        "bg_speech_projection_db": round(leak_db, 2),
        "alpha": None,
        "mix_rms_db": round(rms_db(mix_cut), 2),
        "background_rms_db": round(rms_db(background_cut), 2),
        "background_peak_db": round(peak_db(background_cut), 2),
        "speech_rms_db": round(rms_db(speech_cut), 2),
        "stem_rms_db": {
            name: round(rms_db(stem), 2) for name, stem in sorted(stems.items())
        },
        "ducking_db": ducking_db,
    }
    if ducking_db < 0:
        background_cut = duck_background(background_cut, sample_mask, ducking_db)
    if duck_db > 3.0:
        quality["degraded"] = True
        log.warning("separation_degraded", **{"backend": "bandit_v2", "speech_window_gain_db": round(duck_db, 2)})
    return SeparationResult(
        speech=speech_cut,
        background=background_cut,
        alpha=None,
        mode="stems_sum",
        backend="bandit_v2",
        quality=quality,
    )


def separate_file(
    mix_path: Path,
    speech_out: Path,
    background_out: Path,
    *,
    mode: str | None = None,
    ducking_db: float | None = None,
    fallback: bool = True,
) -> SeparationResult:
    """Прочитать микс, отделить речь, записать две дорожки (float32 WAV).

    Два пути: Bandit v2 (стемы → фон = music + sfx) и Demucs (оценка речи → вычитание
    с подбором α). Если основной бэкенд падает, работаем на fallback и помечаем это.
    """
    mode = mode or settings.separation_mode
    explicit_ducking = ducking_db is not None
    ducking_db = settings.default_ducking_db if ducking_db is None else ducking_db
    bandit_ducking_db = ducking_db if explicit_ducking else settings.bandit_ducking_db

    mix = read(mix_path)
    backend_name = settings.separation_model

    if backend_name == "bandit_v2":
        try:
            result = from_stems(mix, _bandit_stems(mix), ducking_db=bandit_ducking_db)
        except Exception as exc:  # noqa: BLE001
            if not fallback:
                raise
            log.warning("separation_backend_failed", backend=backend_name, error=str(exc))
            result = subtract(mix, BACKENDS["demucs"](mix), mode=mode, ducking_db=ducking_db)
            backend_name = "demucs"
    else:
        result = subtract(mix, BACKENDS[backend_name](mix), mode=mode, ducking_db=ducking_db)

    result.backend = backend_name
    write_float32(speech_out, result.speech)
    write_float32(background_out, result.background)
    log.info("separation_done", backend=backend_name, mode=result.mode, **result.quality)
    return result


def ensure_stereo_speech(speech_path: Path, out_path: Path) -> None:
    """Речь в стерео — нужна для единообразия нарезки и микширования."""
    audio = read(speech_path)
    write_float32(out_path, to_stereo(audio))
