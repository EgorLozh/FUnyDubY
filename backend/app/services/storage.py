"""Хранилище: единственная точка доступа к файлам на диске.

Правила:
* в БД лежат только ОТНОСИТЕЛЬНЫЕ пути от STORAGE_ROOT;
* наружу (в ФС) путь превращается только через safe_join;
* запись — через временный файл в tmp/ комнаты + атомарный rename.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import FileTooLarge, StorageExhausted
from app.core.security import room_dir, room_relative, safe_join

CHUNK = 1024 * 1024  # 1 МБ


def free_bytes(path: Path | None = None) -> int:
    target = path or settings.storage_root
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target)
    return usage.free


def storage_root_is_writable() -> bool:
    """Проверка, что в корень хранилища реально можно писать (не только exists)."""
    root = settings.storage_root
    if not root.exists():
        return False
    probe = root / ".write_probe"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


def ensure_space(needed_bytes: int = 0) -> None:
    if free_bytes() - needed_bytes < settings.min_free_disk_bytes:
        raise StorageExhausted(
            "На сервере заканчивается место под файлы комнат",
            extra={"free_bytes": free_bytes()},
        )


def ensure_room_layout(room_id: str) -> Path:
    """Создать каталог комнаты со всеми подкаталогами схемы §15."""
    root = room_dir(room_id)
    for sub in (
        "original",
        "audio",
        "speech",
        "speech/stems",
        "speech/segments",
        "dialogue",
        "recordings",
        "rendered",
        "tmp",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def absolute(relative: str | Path) -> Path:
    """Относительный путь из БД -> абсолютный путь в ФС (с проверкой границ)."""
    return safe_join(settings.storage_root, relative)


def room_storage_dir(room_id: str) -> Path:
    return absolute(Path("rooms") / room_id)


async def save_upload_stream(
    upload: UploadFile,
    room_id: str,
    *subdir: str,
    filename: str,
    max_bytes: int,
) -> tuple[str, int]:
    """Потоково сохранить загружаемый файл. Возвращает (относительный путь, размер).

    Пишем в tmp/ комнаты, затем атомарно переносим — «половины файла» под рабочим именем не бывает.
    """
    root = ensure_room_layout(room_id)
    ensure_space()
    tmp_dir = root / "tmp"
    tmp_path = tmp_dir / f"{os.getpid()}-{filename}.part"
    final_rel = room_relative(room_id, *subdir, filename)
    final_path = absolute(final_rel)

    written = 0
    try:
        async with aiofiles.open(tmp_path, "wb") as fh:
            while chunk := await upload.read(CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLarge(
                        f"Файл больше допустимого размера ({max_bytes // (1024 * 1024)} МБ)",
                        extra={"limit_bytes": max_bytes},
                    )
                await fh.write(chunk)
            await fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, final_path)
    return final_rel, written


def write_bytes_atomic(room_id: str, data: bytes, *parts: str) -> str:
    root = ensure_room_layout(room_id)
    rel = room_relative(room_id, *parts)
    target = absolute(rel)
    tmp_path = root / "tmp" / f"{target.name}.part"
    tmp_path.write_bytes(data)
    os.replace(tmp_path, target)
    return rel


def delete_room(room_id: str) -> None:
    """Полное удаление каталога комнаты. Никогда не выходит за пределы rooms/{room_id}."""
    root = room_storage_dir(room_id).resolve()
    rooms_root = settings.rooms_root.resolve()
    if rooms_root not in root.parents or root == rooms_root:
        raise RuntimeError(f"Отказ удалять вне корня хранилища: {root}")
    shutil.rmtree(root, ignore_errors=True)


def room_size_bytes(room_id: str) -> int:
    root = room_storage_dir(room_id)
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


async def stream_file(path: Path, start: int = 0, end: int | None = None) -> AsyncIterator[bytes]:
    """Отдача файла куском для Range-запросов."""
    async with aiofiles.open(path, "rb") as fh:
        await fh.seek(start)
        remaining = None if end is None else (end - start + 1)
        while True:
            size = CHUNK if remaining is None else min(CHUNK, remaining)
            if size <= 0:
                break
            chunk = await fh.read(size)
            if not chunk:
                break
            yield chunk
            if remaining is not None:
                remaining -= len(chunk)
