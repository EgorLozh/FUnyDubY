"""Медиа: загруженное видео и записи участников."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class RecordingStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


recording_status_enum = Enum(RecordingStatus, name="recording_status")


class Video(Base, CreatedAtMixin):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    storage_dir: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    container: Mapped[str] = mapped_column(String(32), nullable=False)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probe: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_videos_room", "room_id"),
        Index("ix_videos_sha", "sha256"),
        CheckConstraint("size_bytes > 0", name="ck_videos_size_pos"),
        CheckConstraint("duration_ms between 1 and 3600000", name="ck_videos_duration"),
    )


class Recording(Base, CreatedAtMixin):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dialogue_lines.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )
    take_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RecordingStatus] = mapped_column(
        recording_status_enum, nullable=False, default=RecordingStatus.UPLOADED
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_path: Mapped[str] = mapped_column(Text, nullable=False)
    processed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loudness_lufs: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_version_at_record: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    orphaned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("line_id", "take_number", name="uq_recordings_line_take"),
        Index(
            "uq_recordings_current_per_line",
            "line_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_recordings_participant", "participant_id"),
        CheckConstraint("take_number > 0", name="ck_recordings_take_pos"),
        CheckConstraint("size_bytes > 0", name="ck_recordings_size_pos"),
    )
