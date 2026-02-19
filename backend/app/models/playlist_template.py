"""
PlaylistTemplate + TemplateSlot models — define rotation patterns of asset types/categories.
A template holds ordered slots like: "music/lively → jingle/hourly_id → music/relax → spot/weather".
The scheduling service picks a random matching asset for each slot at play-time.
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlaylistTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "playlist_templates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    slots: Mapped[list["TemplateSlot"]] = relationship(
        "TemplateSlot",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="TemplateSlot.position",
        lazy="selectin",
    )


class TemplateSlot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "template_slots"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playlist_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)  # music, spot, shiur, jingle, zmanim
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)  # lively, relax, etc. Null = any

    template: Mapped["PlaylistTemplate"] = relationship("PlaylistTemplate", back_populates="slots")
