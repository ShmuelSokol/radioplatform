import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Raffle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "raffles"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prize: Mapped[str | None] = mapped_column(String(500), nullable=True)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open", nullable=False)
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_members.id", ondelete="SET NULL"), nullable=True,
    )

    winner = relationship("CrmMember", lazy="noload")
    entries = relationship("RaffleEntry", back_populates="raffle", lazy="noload")


class RaffleEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "raffle_entries"
    __table_args__ = (
        UniqueConstraint("raffle_id", "member_id", name="uq_raffle_entries_raffle_member"),
    )

    raffle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raffles.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_members.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    raffle = relationship("Raffle", back_populates="entries", lazy="noload")
    member = relationship("CrmMember", lazy="noload")
