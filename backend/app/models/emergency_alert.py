from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmergencyAlert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One emergency alert on one station: who or what sent it, when it went on air
    and came off, and how it ended. The station's EAS log on our side (the certified
    EAS box keeps its own, which is the one the FCC asks for). See wiki/emergency-alerts.md."""

    __tablename__ = "emergency_alerts"

    station_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("stations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # "alert" (a real one), "test" (a test of our override, a spoken clip), "rmt"/"rwt"
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="alert")
    # "device" (the EAS box with its token), "staff", "line" (sound on a box's alert line)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="staff")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audio_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The audio holds EAS header tones or the attention signal (eas_tones.detect)
    has_tones: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # sent -> on_air -> done; failed (never reached the engine), cancelled (stopped)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="sent")
    requested_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seconds_on_air: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
