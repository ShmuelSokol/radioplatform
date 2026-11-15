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
    # comma-separated: morning,afternoon,evening,overnight
    dayparts: Mapped[str] = mapped_column(
        String(60), default="morning,afternoon,evening", nullable=False
    )
    plays_per_daypart: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sponsor = relationship("Sponsor", lazy="noload")
    asset = relationship("Asset", lazy="noload")
