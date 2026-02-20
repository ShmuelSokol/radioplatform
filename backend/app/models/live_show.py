import enum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LiveShowStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    ENDED = "ended"
    CANCELLED = "cancelled"


class BroadcastMode(str, enum.Enum):
    WEBRTC = "webrtc"
    ICECAST = "icecast"


class LiveShow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "live_shows"

    station_id: Mapped[UUID] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    host_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[LiveShowStatus] = mapped_column(
        ENUM(LiveShowStatus, name="live_show_status", create_type=True),
        default=LiveShowStatus.SCHEDULED,
        nullable=False,
    )
    broadcast_mode: Mapped[BroadcastMode] = mapped_column(
        ENUM(BroadcastMode, name="broadcast_mode", create_type=True),
        default=BroadcastMode.WEBRTC,
        nullable=False,
    )
    scheduled_start: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_start: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    twilio_conference_sid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    icecast_mount: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calls_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    call_requests = relationship("CallInRequest", back_populates="live_show", lazy="selectin")
