import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SpotSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """When a sponsor's spot airs: which days, which parts of the day, how often.

    Dayparts are in the station's local time (see spot_schedule_service.DAYPARTS).
    """

    __tablename__ = "spot_schedules"

    sponsor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sponsors.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    # NULL = every station
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 0=Mon ... 6=Sun (same convention as schedule_rules)
    days_of_week: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5,6", nullable=False)
    # comma-separated names from spot_schedule_service.DAYPARTS (overnight, the six
    # three-hour slots, morning/afternoon/evening). 60 characters held only four slots:
    # ticking all six gave "value too long" and a server error on Save (2026-11-23).
    dayparts: Mapped[str] = mapped_column(
        String(255), default="morning,afternoon,evening", nullable=False
    )
    plays_per_daypart: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Exact clock times in station time, comma-separated "HH:MM". When set, the
    # spot airs at these times on its days instead of being spread over dayparts.
    fixed_times: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # "hard" = the song on air fades so the spot starts on the second;
    # "soft" = the song finishes first. NULL = the station's default for that slot.
    timing_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sponsor = relationship("Sponsor", lazy="noload")
    asset = relationship("Asset", lazy="noload")
