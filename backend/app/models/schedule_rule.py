import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ScheduleRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "schedule_rules"

    # Optional: if set, this rule applies only to this station.
    # NULL = global rule applied to all stations (unless the station has its own rules).
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # rotation | interval | fixed_time | daypart

    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # music | spot | shiur | jingle | zmanim

    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # sub-category filter (e.g., "med_fast", "shabbos")

    hour_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hour_end: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    days_of_week: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5,6", nullable=False)
    # 0=Mon, 6=Sun

    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    songs_between: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
