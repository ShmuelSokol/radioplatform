import enum
from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ShowArchive(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "show_archives"

    station_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    host_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recorded_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_url: Mapped[str] = mapped_column(Text, nullable=False)
    cover_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    download_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    live_show_id: Mapped[str | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("live_shows.id", ondelete="SET NULL"), nullable=True)

    station = relationship("Station", lazy="noload")
