import enum
import uuid

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class AdCampaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ad_campaigns"

    sponsor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sponsors.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        ENUM(CampaignStatus, name="campaign_status", create_type=True),
        default=CampaignStatus.DRAFT,
        nullable=False,
    )
    start_date = mapped_column(Date, nullable=True)
    end_date = mapped_column(Date, nullable=True)
    budget_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_rules: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    sponsor = relationship("Sponsor", lazy="selectin")
    drafts = relationship("AdDraft", back_populates="campaign", lazy="noload", cascade="all, delete-orphan")
    comments = relationship("AdComment", back_populates="campaign", lazy="noload", cascade="all, delete-orphan")


class AdDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ad_drafts"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    campaign = relationship("AdCampaign", back_populates="drafts")
    creator = relationship("User", lazy="selectin")


class AdComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ad_comments"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_drafts.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    campaign = relationship("AdCampaign", back_populates="comments")
    user = relationship("User", lazy="selectin")
