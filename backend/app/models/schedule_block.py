"""
ScheduleBlock model — time-based scheduling blocks with recurrence rules.
Examples:
  - "Weekday mornings 6-9 AM: Morning playlist"
  - "Friday 3 PM - Sunset: Pre-Sabbath music"
  - "Daily 12-1 PM: Shiur block"
"""
import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, Time, Date
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

from app.models.playlist_entry import PlaybackMode

if TYPE_CHECKING:
    from app.models.schedule import Schedule
    from app.models.playlist_entry import PlaylistEntry


class RecurrenceType(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ONE_TIME = "one_time"


class SunEvent(str, enum.Enum):
    SUNRISE = "sunrise"
    SUNSET = "sunset"
    DAWN = "dawn"
    DUSK = "dusk"


class DayOfWeek(str, enum.Enum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class ScheduleBlock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "schedule_blocks"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Time range (using Time type for daily recurrence; override with date for one-time)
    start_time: Mapped[str] = mapped_column(Time, nullable=False)
    end_time: Mapped[str] = mapped_column(Time, nullable=False)

    # Recurrence
    recurrence_type: Mapped[RecurrenceType] = mapped_column(
        ENUM(RecurrenceType, name="recurrence_type", create_type=True),
        default=RecurrenceType.DAILY,
        nullable=False,
    )
    # For weekly: ["monday", "wednesday", "friday"]
    # For monthly: [1, 15] (day numbers)
    recurrence_pattern: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Priority for conflict resolution within a schedule
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Playback mode for this block's playlist
    playback_mode: Mapped[PlaybackMode] = mapped_column(
        ENUM(PlaybackMode, name="playback_mode", create_type=True),
        default=PlaybackMode.SEQUENTIAL,
        nullable=False,
        server_default="sequential",
    )

    # For ONE_TIME recurrence: date range when this block is active
    start_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[str | None] = mapped_column(Date, nullable=True)

    # Sun-relative time overrides (e.g., "sunset" with offset -30 = 30 min before sunset)
    # When set, these override start_time/end_time with computed values
    start_sun_event: Mapped[SunEvent | None] = mapped_column(
        ENUM(SunEvent, name="sun_event", create_type=True), nullable=True
    )
    start_sun_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)  # minutes
    end_sun_event: Mapped[SunEvent | None] = mapped_column(
        ENUM(SunEvent, name="sun_event", create_type=True), nullable=True
    )
    end_sun_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)  # minutes

    # Relationships
    schedule: Mapped["Schedule"] = relationship("Schedule", back_populates="blocks", lazy="noload")
    playlist_entries: Mapped[list["PlaylistEntry"]] = relationship(
        "PlaylistEntry", back_populates="block", cascade="all, delete-orphan", lazy="noload"
    )
