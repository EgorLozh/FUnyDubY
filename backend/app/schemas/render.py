"""Схемы сборки финальной озвучки."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.models import RenderJob


class RenderOptions(BaseModel):
    """Что делать с репликами, которые никто не озвучил."""

    unrecorded: Literal["silent", "original"] = "silent"
    loudness_lufs: int | None = None


class RenderCreate(BaseModel):
    options: RenderOptions | None = None


class RenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    room_id: str
    status: str
    progress: int
    total_lines: int
    recorded_lines: int
    used_lines: int
    size_bytes: int | None = None
    duration_ms: int | None = None
    is_current: bool
    options: dict[str, Any] = {}
    metrics: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    finished_at: datetime | None = None
    file_url: str | None = None
    download_url: str | None = None


def render_to_out(render: RenderJob) -> RenderOut:
    out = RenderOut.model_validate(render)
    if render.status.value == "DONE":
        out.file_url = f"/api/rooms/{render.room_id}/media/render/{render.id}"
        out.download_url = f"/api/rooms/{render.room_id}/renders/{render.id}/file"
    return out
