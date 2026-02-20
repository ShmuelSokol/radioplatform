import enum
import uuid

from sqlalchemy import Column, Float, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

asset_categories = Table(
    "asset_categories",
    Base.metadata,
    Column("asset_id", UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class AssetType(str, enum.Enum):
    MUSIC = "music"
    SPOT = "spot"
    SHIUR = "shiur"
    JINGLE = "jingle"
    ZMANIM = "zmanim"


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assets"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    album: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    album_art_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    asset_type: Mapped[str] = mapped_column(String(50), default="music", nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(50), default="pending", nullable=True)
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sponsors.id", ondelete="SET NULL"), nullable=True
    )

    created_by_user = relationship("User", back_populates="assets")
    categories = relationship("Category", secondary=asset_categories, back_populates="assets", lazy="selectin")
    sponsor = relationship("Sponsor", lazy="noload")
