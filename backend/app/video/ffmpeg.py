"""Обёртки ffmpeg для этапов конвейера (только списки аргументов, без shell)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger

log = get_logger("ffmpeg")


class FFmpegError(DomainError):
    status_code = 500
    code = "ffmpeg_failed"


def run(args: list[str], *, what: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Запустить ffmpeg/ffprobe. shell=False всегда."""
    cmd = [settings.ffmpeg_bin if what != "ffprobe" else settings.ffprobe_bin, *args]
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or settings.ffmpeg_timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(
            f"ffmpeg не успел за {timeout or settings.ffmpeg_timeout_s} с (шаг: {what})",
            code="ffmpeg_timeout",
        ) from exc
    if result.returncode != 0:
        tail = (result.stderr or "")[-800:]
        log.error("ffmpeg_failed", what=what, returncode=result.returncode, stderr=tail)
        raise FFmpegError(
            f"ffmpeg завершился с ошибкой (шаг: {what})",
            code="ffmpeg_failed",
            extra={"step": what, "exit_code": result.returncode, "stderr_tail": tail},
        )
    return result


def extract_audio(source: Path, out_stereo: Path, out_mono16k: Path) -> None:
    """Извлечь дорожку: 48 кГц стерео PCM16 (для работы) и 16 кГц моно (для STT/диаризации)."""
    out_stereo.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(out_stereo),
        ],
        what="extract_audio_stereo",
    )
    run(
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(out_stereo),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out_mono16k),
        ],
        what="extract_audio_mono16k",
    )


def slice_wav(source: Path, out: Path, start_ms: int, end_ms: int) -> None:
    """Вырезать фрагмент дорожки (нарезка реплик для прослушивания оригинала)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            str(source),
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        what="slice_wav",
    )


def normalize_recording(source: Path, out: Path, target_duration_ms: int, loudness_lufs: int) -> None:
    """Привести запись участника ровно к длительности реплики: обрезать или добить тишиной.

    `atrim` + `apad` гарантируют точную длину, `loudnorm` выравнивает громкость между участниками.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    duration_s = target_duration_ms / 1000
    run(
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-af",
            (
                f"atrim=0:{duration_s:.3f},"
                f"apad=whole_dur={duration_s:.3f},"
                f"loudnorm=I={loudness_lufs}:TP=-1.5:LRA=11"
            ),
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        what="normalize_recording",
    )


def loudness_lufs(source: Path) -> float | None:
    """Измерить громкость файла (EBU R128) — для аудита нормализации."""
    result = run(
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "info",
            "-i",
            str(source),
            "-af",
            "loudnorm=print_format=json",
            "-f",
            "null",
            "-",
        ],
        what="loudness_probe",
    )
    tail = (result.stderr or "").strip().splitlines()
    for line in reversed(tail[-30:]):
        if '"input_i"' in line:
            raw = line.split(":", 1)[1].strip().strip('",')
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def to_wav_lossless(source: Path, out: Path, sample_rate: int = 48000, channels: int = 1) -> None:
    """Декодировать любой вход (webm/ogg/mp4) в WAV — формат, с которым работает numpy-микс."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        what="to_wav",
    )
