import enum

from sqlalchemy import Boolean, Float, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StationType(str, enum.Enum):
    INTERNET = "internet"
    OTA = "ota"
    BOTH = "both"


class Station(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stations"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    type: Mapped[StationType] = mapped_column(
        ENUM(StationType, name="station_type", create_type=True),
        default=StationType.INTERNET,
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    broadcast_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    channels = relationship("ChannelStream", back_populates="station", lazy="selectin")
    schedule_entries = relationship("ScheduleEntry", back_populates="station", lazy="noload")
    play_logs = relationship("PlayLog", back_populates="station", lazy="noload")
    schedules = relationship("Schedule", back_populates="station", lazy="noload")
    now_playing = relationship("NowPlaying", back_populates="station", uselist=False, lazy="noload")
