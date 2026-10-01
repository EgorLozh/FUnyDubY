"""Отдача файлов из хранилища: единая точка для Range-запросов.

Раньше разбор `Range` жил только в видео-роуте и дублировался бы в аудио-роутах. Здесь одна
реализация: `file_response` отдаёт 200 или 206 с корректными заголовками. Медиа наружу
отдаётся только через API — том хранилища не выставлен, поэтому проверка прав происходит
в роутере, до вызова этой функции.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request, status
from fastapi.responses import StreamingResponse

from app.services import storage


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Разобрать `Range: bytes=start-end`. Возвращает (start, end) включительно."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":  # суффикс: последние N байт
            length = int(end_s)
            if length <= 0:
                return None
            start = max(0, size - length)
            return start, size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start >= size:
        return None
    return start, min(end, size - 1)


def file_response(
    request: Request,
    path: Path,
    *,
    content_type: str,
    filename: str | None = None,
    attachment: bool = False,
    cache_seconds: int = 3600,
) -> StreamingResponse:
    """Отдать файл с поддержкой докачки (206 Partial Content)."""
    size = path.stat().st_size
    disposition = "attachment" if attachment else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": f"private, max-age={cache_seconds}",
    }
    if filename:
        headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'

    rng = parse_range(request.headers.get("Range"), size)
    if rng is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(storage.stream_file(path), media_type=content_type, headers=headers)

    start, end = rng
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        storage.stream_file(path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=content_type,
        headers=headers,
    )
