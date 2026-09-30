"""Схемы комнаты и участника."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoomCreate(BaseModel):
    title: str = Field(default="Без названия", max_length=120)
    display_name: str = Field(min_length=1, max_length=40)
    settings: dict[str, Any] | None = None


class RoomSettings(BaseModel):
    """Настройки комнаты: всё, что можно менять без пересборки пайплайна."""

    model_config = ConfigDict(extra="forbid")

    stt_backend: str | None = Field(default=None, pattern="^(whisper|parakeet)$")
    stt_language: str | None = Field(default=None, max_length=8)
    stt_prompt: str | None = Field(default=None, max_length=400)
    max_speakers: int | None = Field(default=None, ge=0, le=20)
    ducking_db: int | None = Field(default=None, ge=-30, le=0)
    unrecorded_policy: str | None = Field(default=None, pattern="^(silent|original)$")
    separation_mode: str | None = Field(
        default=None, pattern="^(subtract_and_duck|subtract_only|stems_sum)$"
    )
    merge_gap_ms: int | None = Field(default=None, ge=50, le=2000)
    max_line_ms: int | None = Field(default=None, ge=2000, le=60000)
    record_tolerance_ms: int | None = Field(default=None, ge=0, le=1000)


class ParticipantCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)


class ParticipantUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    color: str
    is_creator: bool
    last_seen_at: datetime | None = None
    created_at: datetime


class ParticipantRegistered(BaseModel):
    participant_id: uuid.UUID
    token: str
    display_name: str
    color: str
    is_creator: bool


class RoomCounters(BaseModel):
    participants: int = 0
    lines: int = 0
    recorded_lines: int = 0
    assigned_lines: int = 0


class RoomOut(BaseModel):
    id: str
    title: str
    status: str
    settings: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None = None
    counters: RoomCounters
    video: dict[str, Any] | None = None
    job: dict[str, Any] | None = None


class RoomCreated(BaseModel):
    room: RoomOut
    participant_id: uuid.UUID
    token: str
    share_url: str
