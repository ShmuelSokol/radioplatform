from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RuleSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rule_sets"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    categories = relationship("Category", back_populates="ruleset", lazy="selectin")
