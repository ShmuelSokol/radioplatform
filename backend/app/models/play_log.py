import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlaySource(str, enum.Enum):
    SCHEDULER = "scheduler"
    MANUAL = "manual"
    AD = "ad"
    FALLBACK = "fallback"


class PlayLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "play_logs"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[PlaySource] = mapped_column(
        ENUM(PlaySource, name="play_source", create_type=True),
        default=PlaySource.SCHEDULER,
        nullable=False,
    )
    fade_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    station = relationship("Station", back_populates="play_logs")
    asset = relationship("Asset")
