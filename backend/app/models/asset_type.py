from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AssetTypeModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "asset_types"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
