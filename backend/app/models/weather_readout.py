import uuid
from datetime import date, time

from sqlalchemy import Date, ForeignKey, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WeatherReadout(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "weather_readouts"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    readout_date: Mapped[date] = mapped_column(Date, nullable=False)
    script_text: Mapped[str] = mapped_column(Text, nullable=False)
    weather_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    queue_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)

    station = relationship("Station", lazy="selectin")
    asset = relationship("Asset", lazy="selectin")
