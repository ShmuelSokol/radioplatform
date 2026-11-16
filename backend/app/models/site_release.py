from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiteRelease(Base):
    """How open browser tabs move onto a newly published website.

    One row (name "site"). `mode` "auto" lets each tab reload itself at its
    first quiet moment; "manual" keeps every tab on the release it has until
    an admin pushes. A push names the release, when tabs should take it, and
    whether tabs playing audio take it too (at the next item change, then
    picking up where the station is). See wiki/release-pickup.md.
    """
    __tablename__ = "site_release"

    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    push_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    push_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    push_include_playing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
