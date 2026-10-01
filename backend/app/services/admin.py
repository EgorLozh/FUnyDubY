"""Админка: что лежит на диске по комнатам и точечная уборка.

Задача простая и прикладная: посмотреть, какие комнаты есть, сколько каждая занимает и из чего
состоит этот объём, и убрать лишнее, не удаляя комнату целиком. Категории — это каталоги
внутри комнаты:

* `original` — исходное видео;
* `artifacts` — дорожки конвейера и разделение речи (`audio/`, `speech/`), включая нарезки реплик;
* `recordings` — тейки участников;
* `rendered` — готовые сборки.

Уборка «исходного видео» — единственная, которая меняет состояние комнаты: исходник нельзя убрать,
оставив разметку и записи (они ссылаются на несуществующий ролик), поэтому вместе с файлом уходят
джоб, реплики и тейки, а комната возвращается в состояние «загрузите видео». Готовые сборки при этом
остаются скачиваемыми: это отдельные файлы и отдельная категория.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.models import (
    DialogueLine,
    JobStage,
    ProcessingJob,
    Recording,
    RenderJob,
    Room,
    RoomStatus,
    Video,
)
from app.services import storage

log = get_logger("admin")

CATEGORY_DIRS: dict[str, tuple[str, ...]] = {
    "original": ("original",),
    "artifacts": ("audio", "speech"),
    "recordings": ("recordings",),
    "rendered": ("rendered",),
}
SCOPES: tuple[str, ...] = tuple(CATEGORY_DIRS) + ("other",)
PURGEABLE: tuple[str, ...] = tuple(CATEGORY_DIRS)


def _category_of(parts: tuple[str, ...]) -> str:
    head = parts[0] if parts else "other"
    for name, dirs in CATEGORY_DIRS.items():
        if head in dirs:
            return name
    return "other"


def room_usage(room_id: str) -> dict:
    """Размер комнаты: всего, по категориям и число файлов в каждой."""
    root = storage.room_storage_dir(room_id)
    categories: dict[str, int] = {name: 0 for name in SCOPES}
    files: dict[str, int] = {name: 0 for name in SCOPES}
    total = 0
    if not root.exists():
        return {"size_bytes": 0, "categories": categories, "files": files}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        category = _category_of(path.relative_to(root).parts)
        total += size
        categories[category] += size
        files[category] += 1
    return {"size_bytes": total, "categories": categories, "files": files}


def room_files(room_id: str, per_category: int = 200) -> dict[str, list[dict]]:
    """Список файлов комнаты по категориям — что именно занимает место."""
    root = storage.room_storage_dir(room_id)
    grouped: dict[str, list[dict]] = {name: [] for name in SCOPES}
    if not root.exists():
        return grouped
    rows: list[tuple[int, str, dict]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(root)
        category = _category_of(relative.parts)
        rows.append(
            (
                stat.st_size,
                category,
                {
                    "path": relative.as_posix(),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                },
            )
        )
    rows.sort(key=lambda row: row[0], reverse=True)
    for _size, category, item in rows:
        if len(grouped[category]) < per_category:
            grouped[category].append(item)
    return grouped


def _unlink(relative: str | None) -> int:
    if not relative:
        return 0
    try:
        path = storage.absolute(relative)
        if path.is_file():
            size = path.stat().st_size
            path.unlink()
            return size
    except Exception as exc:  # noqa: BLE001 — уборка не должна падать целиком
        log.warning("admin_unlink_failed", path=relative, error=str(exc))
    return 0


def _remove_dir(room_id: str, name: str) -> int:
    """Удалить подкаталог комнаты целиком, вернув освобождённый объём."""
    root = storage.room_storage_dir(room_id)
    target = (root / name).resolve()
    if root.resolve() not in target.parents:
        raise RuntimeError(f"Отказ удалять вне комнаты: {target}")
    if not target.exists():
        return 0
    freed = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    shutil.rmtree(target, ignore_errors=True)
    return freed


async def purge(
    session: AsyncSession, room: Room, scopes: list[str]
) -> dict[str, dict[str, int | str | bool]]:
    """Убрать выбранные категории файлов комнаты. Возвращает отчёт по каждой."""
    unknown = [scope for scope in scopes if scope not in PURGEABLE]
    if unknown:
        raise Conflict(
            f"Неизвестные категории: {', '.join(unknown)}", code="bad_scope"
        )
    if not scopes:
        raise Conflict("Не выбрано, что чистить", code="empty_scope")

    report: dict[str, dict[str, int | str | bool]] = {}

    if "rendered" in scopes:
        freed = 0
        renders = (
            await session.scalars(select(RenderJob).where(RenderJob.room_id == room.id))
        ).all()
        for render in renders:
            freed += _unlink(render.output_path)
            freed += _unlink(render.audio_mix_path)
            render.output_path = None
            render.audio_mix_path = None
            render.files_purged = True
        freed += _remove_dir(room.id, "rendered")
        report["rendered"] = {"freed_bytes": freed, "items": len(renders)}

    if "recordings" in scopes:
        freed = 0
        recordings = (
            await session.scalars(
                select(Recording)
                .join(DialogueLine, DialogueLine.id == Recording.line_id)
                .where(DialogueLine.room_id == room.id)
            )
        ).all()
        for recording in recordings:
            freed += _unlink(recording.raw_path)
            freed += _unlink(recording.processed_path)
            # Тейк пропал: помечаем осиротевшим и снимаем «актуальный», иначе интерфейс
            # обещает запись, которую уже нельзя проиграть.
            recording.orphaned = True
            recording.is_current = False
        freed += _remove_dir(room.id, "recordings")
        report["recordings"] = {"freed_bytes": freed, "items": len(recordings)}

    if "artifacts" in scopes:
        freed = 0
        lines = (
            await session.scalars(
                select(DialogueLine).where(DialogueLine.room_id == room.id)
            )
        ).all()
        for line in lines:
            line.speech_path = None
        freed += _remove_dir(room.id, "audio")
        freed += _remove_dir(room.id, "speech")
        report["artifacts"] = {"freed_bytes": freed, "items": len(lines)}

    if "original" in scopes:
        video = await session.get(Video, room.video_id) if room.video_id else None
        freed = _remove_dir(room.id, "original")
        # Исходника нет — разметка, записи и джоб ссылаются в пустоту, поэтому уходят вместе с ним
        line_ids = (
            await session.scalars(
                select(DialogueLine.id).where(DialogueLine.room_id == room.id)
            )
        ).all()
        if line_ids:
            await session.execute(delete(Recording).where(Recording.line_id.in_(line_ids)))
            await session.execute(delete(DialogueLine).where(DialogueLine.room_id == room.id))
        if video is not None:
            job_ids = (
                await session.scalars(
                    select(ProcessingJob.id).where(ProcessingJob.video_id == video.id)
                )
            ).all()
            if job_ids:
                await session.execute(delete(JobStage).where(JobStage.job_id.in_(job_ids)))
                await session.execute(delete(ProcessingJob).where(ProcessingJob.id.in_(job_ids)))
            await session.delete(video)
        room.video_id = None
        room.status = RoomStatus.CREATED
        room.size_bytes = 0
        report["original"] = {
            "freed_bytes": freed,
            "items": len(line_ids),
            "room_reset": True,
        }

    await session.commit()
    log.info(
        "admin_purge",
        room_id=room.id,
        scopes=scopes,
        freed_bytes=sum(int(item["freed_bytes"]) for item in report.values()),
    )
    return report


async def overview(session: AsyncSession, limit: int = 200) -> dict:
    """Список комнат с размерами и разбивкой по категориям."""
    rooms = (
        await session.scalars(select(Room).order_by(Room.created_at.desc()).limit(limit))
    ).all()
    entries = []
    totals = {"rooms": len(rooms), "size_bytes": 0, "categories": {name: 0 for name in SCOPES}}
    for room in rooms:
        usage = room_usage(room.id)
        video = await session.get(Video, room.video_id) if room.video_id else None
        entries.append(
            {
                "id": room.id,
                "title": room.title,
                "status": room.status.value,
                "created_at": room.created_at,
                "expires_at": room.expires_at,
                "deleted_at": room.deleted_at,
                "size_bytes": usage["size_bytes"],
                "categories": usage["categories"],
                "files": usage["files"],
                "video": (
                    {
                        "duration_ms": video.duration_ms,
                        "size_bytes": video.size_bytes,
                        "container": video.container,
                        "width": video.width,
                        "height": video.height,
                    }
                    if video is not None
                    else None
                ),
            }
        )
        totals["size_bytes"] += usage["size_bytes"]
        for name, value in usage["categories"].items():
            totals["categories"][name] += value
    return {"rooms": entries, "totals": totals}


async def room_detail(session: AsyncSession, room_id: str) -> dict:
    room = await session.get(Room, room_id)
    if room is None:
        raise NotFound("Комната не найдена", code="room_not_found")
    usage = room_usage(room.id)
    video = await session.get(Video, room.video_id) if room.video_id else None
    lines = int(
        await session.scalar(
            select(DialogueLine.id).where(DialogueLine.room_id == room.id).limit(1)
        )
        is not None
    )
    return {
        "id": room.id,
        "title": room.title,
        "status": room.status.value,
        "created_at": room.created_at,
        "expires_at": room.expires_at,
        "size_bytes": usage["size_bytes"],
        "categories": usage["categories"],
        "files": usage["files"],
        "video": (
            {
                "duration_ms": video.duration_ms,
                "size_bytes": video.size_bytes,
                "container": video.container,
                "width": video.width,
                "height": video.height,
            }
            if video is not None
            else None
        ),
        "has_lines": bool(lines),
        "listing": room_files(room.id),
    }


def disk_free_bytes() -> int:
    return storage.free_bytes()
