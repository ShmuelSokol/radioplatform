"""
PlaylistEntry model — associates assets with schedule blocks.
Defines playback order, rotation rules, and weighting.
"""
import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.schedule_block import ScheduleBlock
    from app.models.asset import Asset


class PlaybackMode(str, enum.Enum):
    SEQUENTIAL = "sequential"  # Play in order
    SHUFFLE = "shuffle"  # Random order
    WEIGHTED = "weighted"  # Based on weight field


class PlaylistEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playlist_entries"

    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule_blocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Playback order (for sequential mode)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Weight for weighted random (higher = more frequent)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Playback mode for this entry
    playback_mode: Mapped[PlaybackMode] = mapped_column(
        ENUM(PlaybackMode, name="playback_mode", create_type=True),
        default=PlaybackMode.SEQUENTIAL,
        nullable=False,
    )

    # Enabled flag
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional metadata (e.g., fade in/out, crossfade settings)
    playback_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    block: Mapped["ScheduleBlock"] = relationship("ScheduleBlock", back_populates="playlist_entries", lazy="selectin")
    asset: Mapped["Asset"] = relationship("Asset", lazy="selectin")
