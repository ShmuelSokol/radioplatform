from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SchedulerLease(Base):
    """Which server process is running the playout scheduler right now.

    During a release two copies of the backend are alive for a short while.
    Only the one holding this lease advances the queues, so a song is never
    advanced twice. A lease row rather than a Postgres advisory lock because
    the Supabase transaction pooler does not keep session-level locks.
    """
    __tablename__ = "scheduler_leases"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    holder: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
