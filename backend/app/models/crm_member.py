import uuid

from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class CrmMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_members"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pin: Mapped[str] = mapped_column(String(6), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
