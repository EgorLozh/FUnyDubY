"""Схемы тейков озвучки."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_id: uuid.UUID
    participant_id: uuid.UUID | None = None
    take_number: int
    status: str
    is_current: bool
    duration_ms: int | None = None
    size_bytes: int
    loudness_lufs: float | None = None
    rejected_reason: str | None = None
    created_at: datetime


def recording_to_out(recording) -> RecordingOut:
    return RecordingOut(
        id=recording.id,
        line_id=recording.line_id,
        participant_id=recording.participant_id,
        take_number=recording.take_number,
        status=recording.status.value if hasattr(recording.status, "value") else str(recording.status),
        is_current=recording.is_current,
        duration_ms=recording.duration_ms,
        size_bytes=recording.size_bytes,
        loudness_lufs=float(recording.loudness_lufs) if recording.loudness_lufs is not None else None,
        rejected_reason=recording.rejected_reason,
        created_at=recording.created_at,
    )
