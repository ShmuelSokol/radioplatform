import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, and_, or_, text
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
        Index("ix_queue_entries_station_started", "station_id", "started_at"),
        Index("ix_queue_entries_station_preempt", "station_id", "preempt_at"),
        # Only the waiting / playing rows (the table keeps every entry ever played);
        # existing databases get them from CONCURRENT_INDEXES in app/main.py.
        Index("ix_qe_pending_preempt", "station_id", "preempt_at", "position",
              postgresql_where=text("status = 'pending'")),
        Index("ix_qe_playing_started", "station_id", "started_at",
              postgresql_where=text("status = 'playing'")),
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
    # A soft entry made hard so it could pause a shiur or story on air (long_items).
    # If a live show takes the station first, it goes back to waiting (live_show_service)
    was_soft: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Automatic songs drawn from a schedule block's playlist: that block's id. When
    # unplayed songs are drawn again, the block's place in its playlist goes back
    # by as many, so the playlist carries on from the right step.
    block_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Never read through an entry (code goes by station_id). Loading it with every
    # entry cost two extra queries (the station, then its channels) on each queue
    # read: a third of the scheduler's work on an idle pass. "raise" makes an
    # accidental use fail loudly in tests instead of quietly bringing the cost back.
    station = relationship("Station", lazy="raise")
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


# A cue-in longer than this is not leading silence but a mistake in the analysis
MAX_START_CUE_SECONDS = 30.0


def _cue_in(entry) -> float:
    """The item's start cue (leading silence the players skip), when its asset is loaded.
    Never loads it: callers in async code may not touch an unloaded relationship."""
    from sqlalchemy import inspect as sa_inspect

    try:
        if "asset" in sa_inspect(entry).unloaded:
            return 0.0
    except Exception:
        pass
    asset = getattr(entry, "asset", None)
    if asset is None:
        return 0.0
    from app.models.asset import stored_analysis

    try:
        cue = float(stored_analysis(getattr(asset, "metadata_extra", None)).get("cue_in_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    duration = getattr(asset, "duration", None) or 0.0
    if cue <= 0 or cue > MAX_START_CUE_SECONDS or (duration and cue >= duration - 1):
        return 0.0
    return cue


def start_offset(entry) -> float:
    """Where in the file this entry starts playing: a resumed shiur where it was paused,
    any other item at its cue-in (both players skip the leading silence)."""
    return max(resume_offset(entry), _cue_in(entry))


def mark_started(entry, now) -> None:
    """Put an entry on air. started_at is when the top of the file would have played:
    a resumed item counts as started `offset` seconds ago and a fresh one `cue_in`
    seconds ago, so everything timed from started_at (its end, the hand-over, a
    listener's position) is a position in the file. Counting a fresh item from the
    top made the page and the server differ by the cue-in, and the hand-over came that
    much late (F-HANDOVER-17)."""
    from datetime import timedelta

    entry.status = "playing"
    entry.started_at = now - timedelta(seconds=start_offset(entry))


def aired_from(entry):
    """When this entry really came on air. started_at lies `start_offset` seconds before
    that (see mark_started), which made a resumed item's play log row begin inside the
    row of the first part (Jerusalem, 2026-11-18)."""
    from datetime import timedelta

    started = entry.started_at
    if started is None:
        return None
    return started + timedelta(seconds=start_offset(entry))
