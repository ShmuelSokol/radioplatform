import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StationBlockedAsset(Base):
    """An item one station never plays by itself ("Not on this station").

    The library is shared by every station, so category do_not_play takes an item off
    all of them; a row here takes it off one. Its own table, not a list in
    Station.automation_config: the scheduler pass rewrites those settings and could
    undo a block an operator made a moment earlier, and a table can be used as a
    subquery in every pick (services/station_blocks.py, wiki/control-room.md).
    """
    __tablename__ = "station_blocked_assets"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
