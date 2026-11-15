import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, and_, or_
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class QueueStatus(str, enum.Enum):
    PENDING = "pending"
    PLAYING = "playing"
    PLAYED = "played"
    SKIPPED = "skipped"


class QueueEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "queue_entries"
    __table_args__ = (
        Index("ix_queue_entries_station_status_position", "station_id", "status", "position"),
    )

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_streams.id", ondelete="SET NULL"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How a timed entry (one with preempt_at) goes on air:
    #   "hard" = the item on air is faded out so this starts exactly at preempt_at
    #   "soft" = the item on air finishes, then this plays
    #   None   = the older rule: source "ad_slot" is soft, anything else is hard
    timing_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)

    station = relationship("Station", lazy="selectin")
    asset = relationship("Asset", lazy="selectin")


def soft_timed():
    """SQL condition: a timed entry that waits for the item on air to finish."""
    return or_(
        QueueEntry.timing_mode == "soft",
        and_(QueueEntry.timing_mode.is_(None), QueueEntry.source == "ad_slot"),
    )


def hard_timed():
    """SQL condition: a timed entry that starts exactly at its time (the item on air fades)."""
    return or_(
        QueueEntry.timing_mode == "hard",
        and_(QueueEntry.timing_mode.is_(None), QueueEntry.source != "ad_slot"),
    )


def is_soft_timed(entry) -> bool:
    if entry.timing_mode in ("soft", "hard"):
        return entry.timing_mode == "soft"
    return entry.source == "ad_slot"
