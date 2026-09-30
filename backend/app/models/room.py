"""Комната и участник."""

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RoomStatus(str, enum.Enum):
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    DELETING = "DELETING"


room_status_enum = Enum(RoomStatus, name="room_status")


class Room(Base, TimestampMixin):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="Без названия")
    status: Mapped[RoomStatus] = mapped_column(
        room_status_enum, nullable=False, default=RoomStatus.CREATED
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    video_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("videos.id", ondelete="SET NULL"), nullable=True
    )
    owner_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_rooms_status_expires", "status", "expires_at"),
        Index("ix_rooms_deleted_at", "deleted_at"),
    )


class Participant(Base, TimestampMixin):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#7c9cff")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_creator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("room_id", "display_name", name="uq_participants_room_name"),
        Index("ix_participants_room", "room_id"),
        CheckConstraint("length(display_name) between 1 and 40", name="ck_participants_name_len"),
    )
