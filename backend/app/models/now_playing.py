"""
NowPlaying model — tracks current playback state per station.
Used for WebSocket broadcasts and "what's playing now" queries.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NowPlaying(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "now_playing"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Playback timing
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Optional: reference to schedule block that triggered this
    block_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule_blocks.id", ondelete="SET NULL"), nullable=True
    )

    # Optional: listener count (can be updated by stream server)
    listener_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # Stream URL or playback URL
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    station = relationship("Station", lazy="selectin")
    asset = relationship("Asset", lazy="selectin")
    block = relationship("ScheduleBlock", lazy="selectin")
