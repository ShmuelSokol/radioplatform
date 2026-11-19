import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class HolidayItem(UUIDPrimaryKeyMixin, Base):
    """One song or shiur on a holiday's list (Chanukah, Pesach...).

    Before and during its holiday the station's ordinary picks are now and then
    replaced by an item of the same kind from this list (services/holiday_music.py,
    wiki/holiday-music.md). Outside the season the item does not air by itself at
    all, unless `all_year` is set: a Chanukah song in July is wrong.
    `station_id` null = every station; set = that station only.
    """
    __tablename__ = "holiday_items"

    holiday: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=True
    )
    all_year: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HolidaySetting(Base):
    """How one station mixes in one holiday's list. No row = the defaults
    (services/holiday_music.py DEFAULTS). Its own table, not automation_config:
    the scheduler pass rewrites those settings."""
    __tablename__ = "holiday_settings"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True
    )
    holiday: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Days before the holiday's first day that the mix starts
    lead_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    # 1 holiday item in every N ordinary items: at the start of the lead time, on the
    # day before the holiday (the peak), and on the holiday's own days on the air
    start_every: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    peak_every: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    during_every: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    include_shiurim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "end": through the holiday's last day; "start": stops when the holiday begins
    stop: Mapped[str] = mapped_column(String(10), nullable=False, default="end")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
