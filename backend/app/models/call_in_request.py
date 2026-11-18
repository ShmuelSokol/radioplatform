import enum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CallStatus(str, enum.Enum):
    WAITING = "waiting"
    SCREENING = "screening"
    APPROVED = "approved"
    ON_AIR = "on_air"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ABANDONED = "abandoned"


class CallInRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "call_in_requests"

    live_show_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_shows.id", ondelete="CASCADE"), nullable=False
    )
    caller_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    caller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[CallStatus] = mapped_column(
        ENUM(CallStatus, name="call_status", create_type=True),
        default=CallStatus.WAITING,
        nullable=False,
    )
    twilio_call_sid: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    hold_start: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    air_start: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    air_end: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who is calling, the same on every call of theirs ("tel:<number>" or "web:<id>"):
    # the key of their history and standing (app/services/caller_history.py)
    caller_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # Locked by the host or screener: End Call, Hold, Reject and Next leave this call
    # alone until it is unlocked (a guest who must not be dropped by a slip of the hand)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    screened_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    live_show = relationship("LiveShow", back_populates="call_requests")
