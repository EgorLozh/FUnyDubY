"""Схемы реплик диалога, спикеров и назначений."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    idx: int
    start_ms: int
    end_ms: int
    duration_ms: int
    speaker_key: str
    speaker_label: str
    text: str
    is_short: bool
    overlaps: bool
    is_edited: bool
    keep_original: bool
    version: int
    has_original_audio: bool
    words: list[dict[str, Any]] | None = None
    assigned_participant_id: uuid.UUID | None = None
    assigned_display_name: str | None = None
    assigned_expires_at: datetime | None = None
    has_recording: bool = False
    current_recording_id: uuid.UUID | None = None
    recording_duration_ms: int | None = None


class SpeakerOut(BaseModel):
    speaker_key: str
    speaker_label: str
    lines: int
    total_ms: int


class LineUpdateIn(BaseModel):
    """Правка реплики. `expected_version` включает оптимистическую блокировку."""

    text: str | None = Field(default=None, max_length=4000)
    speaker_label: str | None = Field(default=None, min_length=1, max_length=64)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=1)
    expected_version: int | None = Field(default=None, ge=1)


class LineSplitIn(BaseModel):
    at_ms: int = Field(ge=0, description="Точка разделения внутри реплики")


class LineMergeIn(BaseModel):
    """Склейка со следующей репликой — тело не требуется."""


class SpeakerPatchIn(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    merge_into: str | None = Field(
        default=None, description="Ключ спикера, с которым объединить текущего"
    )


class AssignmentIn(BaseModel):
    participant_id: uuid.UUID | None = Field(
        default=None, description="Кому назначить; по умолчанию — тому, кто запрашивает"
    )
    expected_version: int | None = Field(default=None, ge=1)


class AssignmentOut(BaseModel):
    line_id: uuid.UUID
    participant_id: uuid.UUID
    display_name: str | None = None
    assigned_at: datetime
    expires_at: datetime | None
    version: int


class BulkAssignmentIn(BaseModel):
    speaker_key: str | None = None
    line_ids: list[uuid.UUID] | None = None
    scope: str = Field(default="all-unassigned", pattern="^(all-unassigned|my-speaker|line-ids)$")
    participant_id: uuid.UUID | None = None


class BulkAssignmentOut(BaseModel):
    captured: list[uuid.UUID]
    already_mine: list[uuid.UUID]
    busy: list[uuid.UUID]


def line_to_out(
    line,
    *,
    assigned_participant_id: uuid.UUID | None = None,
    assigned_display_name: str | None = None,
    assigned_expires_at: datetime | None = None,
    current_recording=None,
) -> LineOut:
    return LineOut(
        id=line.id,
        idx=line.idx,
        start_ms=line.start_ms,
        end_ms=line.end_ms,
        duration_ms=line.end_ms - line.start_ms,
        speaker_key=line.speaker_key,
        speaker_label=line.speaker_label,
        text=line.text,
        is_short=line.is_short,
        overlaps=line.overlaps,
        is_edited=line.is_edited,
        keep_original=line.keep_original,
        version=line.version,
        has_original_audio=bool(line.speech_path),
        words=line.words,
        assigned_participant_id=assigned_participant_id,
        assigned_display_name=assigned_display_name,
        assigned_expires_at=assigned_expires_at,
        has_recording=current_recording is not None,
        current_recording_id=getattr(current_recording, "id", None),
        recording_duration_ms=getattr(current_recording, "duration_ms", None),
    )
