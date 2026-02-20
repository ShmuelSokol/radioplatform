import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class QueueStatus(str, enum.Enum):
    PENDING = "pending"
    PLAYING = "playing"
    PLAYED = "played"
    SKIPPED = "skipped"


class QueueEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "queue_entries"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_streams.id", ondelete="SET NULL"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    station = relationship("Station", lazy="selectin")
    asset = relationship("Asset", lazy="selectin")
