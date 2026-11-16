"""A bug report sent from the site's "Report a bug" button. See wiki/bug-reports.md."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# new -> sent to GitHub (mirrored) -> fix_ready / needs_owner -> fixed / closed
BUG_STATUSES = ("new", "mirrored", "fix_ready", "needs_owner", "fixed", "closed")


class BugReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bug_reports"

    number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")

    reporter_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reporter_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    station_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # What the browser saw: player, audio elements, queue, console, network
    client_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Server log lines and requests from the minutes before the report
    server_log: Mapped[dict | None] = deferred(mapped_column(JSONB, nullable=True))
    screenshot: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    screenshot_mime: Mapped[str | None] = mapped_column(String(40), nullable=True)

    github_issue: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fix_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mirror_error: Mapped[str | None] = mapped_column(Text, nullable=True)
