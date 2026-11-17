import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HsSoftened(Base):
    """A timed queue item that HS off made wait for the song, and how it was before.

    One row per item, so switching HS back on restores every item exactly. Kept in
    its own table, not in Station.automation_config: the scheduler pass would
    otherwise rewrite the station's whole settings and could undo a change an
    operator made a moment earlier (wiki/station-modes.md).
    """
    __tablename__ = "hs_softened"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("queue_entries.id", ondelete="CASCADE"), primary_key=True
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timing_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    hard_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
