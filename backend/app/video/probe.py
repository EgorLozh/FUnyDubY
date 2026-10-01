"""ffprobe/ffmpeg: единственная точка работы с внешними бинарниками.

Правила безопасности:
* никаких shell-строк — только список аргументов;
* имена файлов всегда наши (генерируются сервером), пользовательский ввод в аргументы не попадает;
* обязательный таймаут и захват stderr в лог.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.errors import CorruptMedia, NoAudioTrack, UnsupportedFormat, VideoTooLong
from app.core.logging import get_logger

log = get_logger("ffmpeg")

# Токены format_name из ffprobe, которые считаем допустимыми контейнерами MVP
ALLOWED_FORMAT_TOKENS = {"mp4", "mov", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"}


@dataclass
class MediaInfo:
    container: str
    duration_ms: int
    has_audio: bool
    audio_codec: str | None
    video_codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    rotation: int
    has_video: bool
    probe: dict

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000


def _run(args: list[str], timeout: int, what: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 — список аргументов, shell=False
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log.error("ffmpeg_timeout", what=what, timeout=timeout)
        raise CorruptMedia(
            f"Обработка файла заняла больше {timeout} секунд ({what})",
            code="ffmpeg_timeout",
        ) from exc


def _parse_fps(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _rotation(stream: dict) -> int:
    """Поворот может лежать в tags.rotate (старый способ) или в side_data_list."""
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except (TypeError, ValueError):
            pass
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(float(side["rotation"])) % 360
            except (TypeError, ValueError):
                continue
    return 0


def probe(path: Path, timeout: int | None = None) -> MediaInfo:
    """Прочитать метаданные файла. Не падает на странных файлах — отдаёт то, что есть."""
    result = _run(
        [
            settings.ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout or 60,
        "ffprobe",
    )
    if result.returncode != 0:
        log.warning("ffprobe_failed", returncode=result.returncode, stderr=result.stderr[-500:])
        raise CorruptMedia("Не удалось прочитать видеофайл — возможно, он повреждён")

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise CorruptMedia("Не удалось разобрать метаданные файла") from exc

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_s = 0.0
    for candidate in (fmt.get("duration"), (video or {}).get("duration"), (audio or {}).get("duration")):
        try:
            duration_s = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    return MediaInfo(
        container=str(fmt.get("format_name") or ""),
        duration_ms=int(round(duration_s * 1000)),
        has_audio=audio is not None,
        audio_codec=(audio or {}).get("codec_name"),
        video_codec=(video or {}).get("codec_name"),
        width=(video or {}).get("width"),
        height=(video or {}).get("height"),
        fps=_parse_fps((video or {}).get("avg_frame_rate")),
        rotation=_rotation(video or {}),
        has_video=video is not None,
        probe=data,
    )


def audio_duration_ms(path: Path, timeout: int | None = None) -> int:
    """Длительность аудио в мс, устойчивая к контейнерам без заголовка длины.

    `format=duration` есть не везде: webm, записанный браузерным MediaRecorder (и вообще запись
    в неищущийся поток), не содержит элемента Duration, и ffprobe отвечает «N/A». Опираться на
    это нельзя — именно на этой цифре стоит лимит «запись не длиннее реплики», и с N/A проверка
    молча пропускала любую длину. Поэтому при отсутствии метаданных измеряем декодированием:
    гоним поток в null и берём последний `time=` из вывода ffmpeg.
    """
    import re

    info = probe(path, timeout=timeout)
    if info.duration_ms > 0:
        return info.duration_ms

    limit = timeout or settings.ffmpeg_timeout_s
    process = _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-f", "null", "-"],
        limit,
        "audio_duration",
    )
    matches = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", process.stderr or "")
    if not matches:
        raise CorruptMedia(
            "Не удалось определить длительность записи — файл повреждён или обрезан",
            code="duration_unknown",
        )
    hours, minutes, seconds = matches[-1]
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


def ensure_decodable(path: Path, seconds: int = 3) -> None:
    """Пробное декодирование первых секунд: ловит обрезанные и битые файлы."""
    result = _run(
        [
            settings.ffmpeg_bin,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-t",
            str(seconds),
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ],
        timeout=120,
        what="decode_check",
    )
    if result.returncode != 0:
        log.warning("decode_check_failed", stderr=result.stderr[-500:])
        raise CorruptMedia("Видео не декодируется — файл повреждён или обрезан")


def validate_upload(path: Path) -> MediaInfo:
    """Полная валидация загруженного файла: контейнер, длительность, звук, декодируемость."""
    info = probe(path)

    tokens = {t.strip() for t in info.container.split(",") if t.strip()}
    if not tokens & ALLOWED_FORMAT_TOKENS:
        raise UnsupportedFormat(
            "Формат не поддерживается. Нужны MP4, WebM или MOV",
            extra={"detected_container": info.container or "unknown"},
        )
    if not info.has_video:
        raise UnsupportedFormat("В файле нет видеодорожки", code="no_video_stream")
    if info.duration_ms <= 0:
        raise CorruptMedia("Не удалось определить длительность видео")
    if info.duration_ms > settings.max_video_ms:
        raise VideoTooLong(
            f"Видео длится {_mmss(info.duration_ms)} — лимит {settings.max_video_minutes}:00",
            extra={"duration_ms": info.duration_ms, "limit_ms": settings.max_video_ms},
        )
    if not info.has_audio:
        raise NoAudioTrack("В видео нет звуковой дорожки — озвучивать нечего")

    ensure_decodable(path)
    return info


def _mmss(ms: int) -> str:
    total = ms // 1000
    return f"{total // 60}:{total % 60:02d}"
