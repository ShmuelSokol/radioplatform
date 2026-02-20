import uuid

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Sponsor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sponsors"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    length_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    target_rules: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    insertion_policy: Mapped[str] = mapped_column(String(50), default="between_tracks", nullable=False)

    # Link to user account (for sponsor portal login)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user = relationship("User", lazy="noload")
