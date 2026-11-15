import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SponsorshipRules(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The station's sponsorship wording rules. Every save adds a row;
    the highest version is the one in force, older rows are the history."""

    __tablename__ = "sponsorship_rules"

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
