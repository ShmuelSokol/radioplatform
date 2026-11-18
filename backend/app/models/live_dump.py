from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LiveDump(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One press of Dump during a live show: who, when, how much was cut, and a
    recording of it (what listeners heard just before, then what they never heard),
    so the station can review afterwards what was kept off the air."""

    __tablename__ = "live_dumps"

    live_show_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_shows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    station_id: Mapped[UUID] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dumped_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    # Seconds cut (never aired) and seconds of what aired just before, kept for context
    cut_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    heard_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    caller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # the caller dropped with it
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)   # WAV: heard part, then cut part
