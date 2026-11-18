"""RDS (the text FM radios show): per-station settings, scheduled messages and
the log of what the RDS encoder actually picked up. See wiki/rds-feed.md."""
import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RdsSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per station. No row = the defaults in rds_service.DEFAULTS."""

    __tablename__ = "rds_settings"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Programme Service name: the 8 characters a radio shows as the station's name
    ps_text: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # "static" (recommended; PS never changes) or "rotate" (messages may carry their own PS)
    ps_mode: Mapped[str] = mapped_column(String(10), default="static", nullable=False)
    # How long each RadioText line stays up before the next one in the rotation
    rotation_seconds: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    # Weight of "now playing" in the rotation; 0 = never shown in the rotation
    now_playing_weight: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # While a sponsor's spot airs, show that sponsor's RDS line instead of the rotation
    sponsor_while_spot_airs: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Shown when nothing else has text (jingles, Hebrew-only titles, empty rotation)
    fallback_text: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RdsMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A RadioText line in the rotation: slogan, time, weather, sponsor line or any text."""

    __tablename__ = "rds_messages"

    # NULL = every station
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # text | sponsor | time | weather (the kind only labels it; placeholders do the work)
    kind: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    # Template, up to 64 characters once filled in. Placeholders: {time} {date} {day}
    # {temp} {weather} {station}
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Used only when the station's ps_mode is "rotate"
    ps_text: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sponsors.id", ondelete="CASCADE"), nullable=True
    )
    # How many turns in the rotation compared to the others (1 = once per round)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 0=Mon ... 6=Sun, same as spot schedules
    days_of_week: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5,6", nullable=False)
    # Station-local daily window; end before start = runs past midnight. NULL = all day
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RdsPlay(UUIDPrimaryKeyMixin, Base):
    """A line the RDS encoder fetched: first and last time it was fetched in a row."""

    __tablename__ = "rds_plays"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rds_messages.id", ondelete="SET NULL"), nullable=True
    )
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sponsors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # now_playing | message | sponsor_on_air | fallback
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(String(128), nullable=False)
    ps_text: Mapped[str | None] = mapped_column(String(8), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetches: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
