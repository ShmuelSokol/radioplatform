from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class HolidayWindow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "holiday_windows"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_blackout: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    affected_stations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    replacement_content: Mapped[str | None] = mapped_column(Text, nullable=True)
