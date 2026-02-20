import enum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, enum.Enum):
    SCHEDULE_CONFLICT = "schedule_conflict"
    PLAYBACK_GAP = "playback_gap"
    QUEUE_EMPTY = "queue_empty"
    ASSET_MISSING = "asset_missing"
    STREAM_DOWN = "stream_down"
    BLACKOUT_START = "blackout_start"
    BLACKOUT_END = "blackout_end"
    SYSTEM = "system"
    LIVE_SHOW = "live_show"


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"

    station_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("stations.id", ondelete="SET NULL"), nullable=True
    )
    alert_type: Mapped[AlertType] = mapped_column(
        ENUM(AlertType, name="alert_type", create_type=True), nullable=False
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        ENUM(AlertSeverity, name="alert_severity", create_type=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
