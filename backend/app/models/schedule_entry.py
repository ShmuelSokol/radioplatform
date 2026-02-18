import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ScheduleEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "schedule_entries"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    start_time: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    recurrence_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    station = relationship("Station", back_populates="schedule_entries")
