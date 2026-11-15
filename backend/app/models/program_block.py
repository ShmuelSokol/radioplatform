import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProgramBlock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named run of items that airs as one event at set clock times.

    Like "Weather Sponsors" in the studio program: one line in the schedule
    that expands into a station ID, the weather, a PSA, a sponsor spot and a
    programme segment. See wiki/program-blocks.md.
    """

    __tablename__ = "program_blocks"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 0=Mon ... 6=Sun, comma-separated (same as spot schedules)
    days_of_week: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5,6", nullable=False)
    # Station-time clock times, comma-separated "HH:MM"
    times: Mapped[str] = mapped_column(String(400), nullable=False)
    # hard = the song on air fades and the block starts on the second; soft = the song finishes
    timing_mode: Mapped[str] = mapped_column(String(10), default="hard", nullable=False)
    # Ordered list of items, each one of:
    #   {"kind": "asset", "asset_id": "..."}                    one particular recording
    #   {"kind": "category", "asset_type": "...", "category": "..."}  the longest-rested of a type
    #   {"kind": "time"}                                        the time announcement for that hour
    #   {"kind": "weather"}                                     that day's weather
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
