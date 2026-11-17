import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EngineHandoff(Base):
    """A queue entry handed to the playout engine, and its asset.

    The engine may start an entry after a schedule redraw deleted it here. Its start
    report then needs the asset to show what is on air. Kept in the database, not
    only in memory, so a backend restart between the hand-over and the start report
    does not leave the station showing nothing (Offline) while the engine plays.
    No foreign key on `entry_id`: the row must outlive the deleted entry. Rows older
    than a few hours are pruned by the scheduler tick.
    """
    __tablename__ = "engine_handoffs"

    entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    handed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
