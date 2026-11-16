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
    # Soft entries only: if the item on air is still playing at this time, fade it
    # and start (a soft start with a limit). None = wait for the item to finish.
    hard_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Automatic songs drawn from a schedule block's playlist: that block's id. When
    # unplayed songs are drawn again, the block's place in its playlist goes back
    # by as many, so the playlist carries on from the right step.
    block_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

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


# ── Resuming an item that a timed item paused ──
# A shiur cut by the hourly time check is queued again right after it, marked with
# source "resume@<seconds>": where it was stopped. Encoded in `source` so no new
# column is needed (a schema change on this busy table is what froze a deploy).
RESUME_PREFIX = "resume@"


def resume_source(seconds: float) -> str:
    return f"{RESUME_PREFIX}{int(max(0.0, seconds))}"


def resume_offset(entry) -> float:
    """Seconds into the item where this entry starts (0 for anything but a resume)."""
    source = getattr(entry, "source", None) or ""
    if not source.startswith(RESUME_PREFIX):
        return 0.0
    try:
        return max(0.0, float(source[len(RESUME_PREFIX):]))
    except ValueError:
        return 0.0


def mark_started(entry, now) -> None:
    """Put an entry on air. A resumed item counts as started `offset` seconds ago, so
    everything timed from started_at (its end, a listener's position) continues where
    it was paused."""
    from datetime import timedelta

    entry.status = "playing"
    entry.started_at = now - timedelta(seconds=resume_offset(entry))
