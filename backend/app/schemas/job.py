"""Схемы джобов обработки."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    scope: str = Field(default="all", pattern="^(all|extract_audio|separate_speech|transcribe|diarize|merge_dialogue)$")


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage: str
    status: str
    attempt: int
    progress: int
    duration_ms: int | None = None
    metrics: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class JobOut(BaseModel):
    id: uuid.UUID
    room_id: str
    status: str
    current_stage: str | None = None
    progress: int
    scope: str
    attempt: int
    error: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stages: list[StageOut] = []
