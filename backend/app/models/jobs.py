"""Джобы обработки видео и рендера."""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    DONE = "DONE"


class StageStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class StageName(str, enum.Enum):
    EXTRACT_AUDIO = "extract_audio"
    SEPARATE_SPEECH = "separate_speech"
    TRANSCRIBE = "transcribe"
    DIARIZE = "diarize"
    MERGE_DIALOGUE = "merge_dialogue"


class RenderStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    MIXING = "MIXING"
    ENCODING = "ENCODING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


job_status_enum = Enum(JobStatus, name="job_status")
stage_status_enum = Enum(StageStatus, name="stage_status")
stage_name_enum = Enum(StageName, name="stage_name")
render_status_enum = Enum(RenderStatus, name="render_status")

STAGE_WEIGHTS: dict[str, int] = {
    StageName.EXTRACT_AUDIO.value: 5,
    StageName.SEPARATE_SPEECH.value: 35,
    StageName.TRANSCRIBE.value: 35,
    StageName.DIARIZE.value: 20,
    StageName.MERGE_DIALOGUE.value: 5,
}


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(job_status_enum, nullable=False, default=JobStatus.QUEUED)
    current_stage: Mapped[StageName | None] = mapped_column(stage_name_enum, nullable=True)
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Идемпотентность: на одно видео — один активный джоб
        UniqueConstraint("video_id", name="uq_processing_jobs_video"),
        Index("ix_processing_jobs_room_status", "room_id", "status"),
        CheckConstraint("progress between 0 and 100", name="ck_jobs_progress"),
    )


class JobStage(Base, TimestampMixin):
    __tablename__ = "job_stages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[StageName] = mapped_column(stage_name_enum, nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        stage_status_enum, nullable=False, default=StageStatus.PENDING
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "stage", "attempt", name="uq_job_stages_attempt"),
        Index("ix_job_stages_job", "job_id"),
        CheckConstraint("progress between 0 and 100", name="ck_stages_progress"),
    )


class RenderJob(Base, TimestampMixin):
    __tablename__ = "render_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RenderStatus] = mapped_column(
        render_status_enum, nullable=False, default=RenderStatus.QUEUED
    )
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    options_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lines_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recorded_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_mix_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Метрики сборки: сколько тейков ушло в микс, истинная громкость, сработавший потолок.
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("room_id", "options_hash", "lines_version", name="uq_render_idempotency"),
        Index(
            "uq_render_current_per_room", "room_id", unique=True, postgresql_where=text("is_current")
        ),
        Index("ix_render_jobs_room_created", "room_id", "created_at"),
    )
