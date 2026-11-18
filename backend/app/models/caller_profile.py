from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CallerProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What the staff decided about one caller, across shows: priority (listed first),
    blocked (their calls are refused) and a note about the person. `key` is the
    caller's identity: "tel:<number>" for a phone, "web:<id>" for a browser
    (see app/services/caller_history.py)."""

    __tablename__ = "caller_profiles"

    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    standing: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
