import enum
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    QUEUED = "queued"
    PLAYED = "played"
    REJECTED = "rejected"


class SongRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "song_requests"

    station_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requester_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    song_title: Mapped[str] = mapped_column(String(500), nullable=False)
    song_artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[RequestStatus] = mapped_column(
        ENUM(RequestStatus, name="request_status", create_type=True),
        default=RequestStatus.PENDING,
        nullable=False,
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    station = relationship("Station", lazy="noload")
    asset = relationship("Asset", lazy="noload")
