import enum

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    VIEWER = "viewer"
    SPONSOR = "sponsor"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        ENUM(UserRole, name="user_role", create_type=True),
        default=UserRole.VIEWER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    alert_preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    social_links: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    assets = relationship("Asset", back_populates="created_by_user", lazy="noload")
