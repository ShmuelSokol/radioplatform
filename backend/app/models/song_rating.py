import uuid

from sqlalchemy import Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class SongRating(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "song_ratings"
    __table_args__ = (
        UniqueConstraint("member_id", "asset_id", name="uq_song_ratings_member_asset"),
    )

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_members.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    member = relationship("CrmMember", lazy="noload")
    asset = relationship("Asset", lazy="noload")
