from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Sponsor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sponsors"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    length_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    target_rules: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    insertion_policy: Mapped[str] = mapped_column(String(50), default="between_tracks", nullable=False)
