import uuid

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    preview_start_seconds: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    preview_end_seconds: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    default_silence_threshold_db: Mapped[float] = mapped_column(Float, default=-30.0, nullable=False)
    default_silence_min_duration: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    extra_preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
