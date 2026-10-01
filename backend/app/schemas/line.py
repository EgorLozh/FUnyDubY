"""Схемы реплик диалога."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


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


class SpeakerOut(BaseModel):
    speaker_key: str
    speaker_label: str
    lines: int
    total_ms: int


def line_to_out(line) -> LineOut:
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
    )
