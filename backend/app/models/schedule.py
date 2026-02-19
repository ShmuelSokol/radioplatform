"""
Schedule model — top-level container for a station's schedule configuration.
Each schedule can have multiple blocks (time slots) with associated playlists.
"""
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.station import Station
    from app.models.schedule_block import ScheduleBlock


class Schedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "schedules"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Priority for conflict resolution (higher = wins)
    priority: Mapped[int] = mapped_column(default=0, nullable=False)

    # Relationships
    station: Mapped["Station"] = relationship("Station", back_populates="schedules", lazy="selectin")
    blocks: Mapped[list["ScheduleBlock"]] = relationship(
        "ScheduleBlock", back_populates="schedule", cascade="all, delete-orphan", lazy="selectin"
    )
