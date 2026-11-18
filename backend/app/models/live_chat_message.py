from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LiveChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One line of the staff chat of a live show: the screener and the host write to
    each other without a word going on air ("caller 2 is ready", "wrap up in 1 min")."""

    __tablename__ = "live_chat_messages"

    live_show_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_shows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
