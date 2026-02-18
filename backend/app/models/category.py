import uuid

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    ruleset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_sets.id", ondelete="SET NULL"), nullable=True
    )
    allowed_transitions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    min_play_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    fade_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    ruleset = relationship("RuleSet", back_populates="categories")
    assets = relationship(
        "Asset", secondary="asset_categories", back_populates="categories", lazy="selectin"
    )
