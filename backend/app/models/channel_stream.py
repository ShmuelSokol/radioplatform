import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ChannelStream(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_streams"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bitrate: Mapped[int] = mapped_column(Integer, default=128, nullable=False)
    codec: Mapped[str] = mapped_column(String(50), default="aac", nullable=False)
    hls_manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    listeners_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="true")

    # Optional: dedicated schedule for this channel (if null, uses station default)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )

    station = relationship("Station", back_populates="channels")
    schedule = relationship("Schedule", lazy="noload")
