"""Схемы админки: обзор комнат и уборка файлов."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

PurgeScope = Literal["original", "artifacts", "recordings", "rendered"]


class AdminVideo(BaseModel):
    duration_ms: int | None = None
    size_bytes: int | None = None
    container: str | None = None
    width: int | None = None
    height: int | None = None


class AdminOrphansSummary(BaseModel):
    count: int = 0
    size_bytes: int = 0


class AdminRoom(BaseModel):
    id: str
    title: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    size_bytes: int
    categories: dict[str, int]
    files: dict[str, int]
    video: AdminVideo | None = None
    has_lines: bool | None = None
    listing: dict[str, list[dict[str, Any]]] | None = None


class AdminTotals(BaseModel):
    rooms: int
    size_bytes: int
    categories: dict[str, int]


class AdminOverview(BaseModel):
    rooms: list[AdminRoom]
    totals: AdminTotals
    disk_free_bytes: int
    orphans: AdminOrphansSummary = AdminOrphansSummary()


class AdminOrphan(BaseModel):
    id: str
    size_bytes: int
    files: int
    modified_at: str


class AdminOrphans(BaseModel):
    orphans: list[AdminOrphan]
    size_bytes: int
    disk_free_bytes: int


class AdminOrphanPurgeRequest(BaseModel):
    """ids не заданы — убираем все каталоги без комнаты."""

    ids: list[str] | None = None


class AdminOrphanPurgeResult(BaseModel):
    removed: list[str]
    freed_bytes: int
    size_bytes_after: int


class AdminPurgeRequest(BaseModel):
    scopes: list[PurgeScope]


class AdminPurgeResult(BaseModel):
    room_id: str
    scopes: dict[str, dict[str, Any]]
    freed_bytes: int
    size_bytes_after: int
    categories_after: dict[str, int]


class AdminRoomDeleted(BaseModel):
    room_id: str
    status: str
