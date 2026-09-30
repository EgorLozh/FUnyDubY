"""Схемы видео."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    container: str
    video_codec: str | None = None
    audio_codec: str | None = None
    size_bytes: int
    duration_ms: int
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool
    sha256: str | None = None
    created_at: datetime
