"""Реплики диалога, назначения и журнал событий комнаты."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin


class DialogueLine(Base, TimestampMixin):
    __tablename__ = "dialogue_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_key: Mapped[str] = mapped_column(String(32), nullable=False, default="spk_0")
    speaker_label: Mapped[str] = mapped_column(String(64), nullable=False, default="Speaker 1")
    text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    words: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_short: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    overlaps: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    keep_original: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    speech_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_lines_room_idx", "room_id", "idx", unique=True),
        Index("ix_lines_room_start", "room_id", "start_ms"),
        Index("ix_lines_room_speaker", "room_id", "speaker_key"),
        CheckConstraint("start_ms >= 0", name="ck_lines_start_nonneg"),
        CheckConstraint("end_ms > start_ms", name="ck_lines_end_gt_start"),
        CheckConstraint("end_ms - start_ms between 100 and 60000", name="ck_lines_duration_range"),
    )

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class Assignment(Base, TimestampMixin):
    """Одна реплика — максимум один держатель: line_id это PRIMARY KEY."""

    __tablename__ = "assignments"

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dialogue_lines.id", ondelete="CASCADE"),
        primary_key=True,
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_assignments_participant", "participant_id"),
        Index("ix_assignments_expires", "expires_at"),
    )


class RoomEvent(Base, CreatedAtMixin):
    """Журнал событий комнаты: SSE-ресинхронизация, отладка, история."""

    __tablename__ = "room_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_room_events_room_id", "room_id", "id"),)
