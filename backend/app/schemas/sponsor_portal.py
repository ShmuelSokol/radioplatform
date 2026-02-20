"""Pydantic schemas for the Sponsor Portal."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PlayHistoryEntry(BaseModel):
    id: UUID | str
    station_name: str
    asset_title: str
    start_utc: datetime
    end_utc: datetime | None = None
    duration_seconds: float | None = None


class PlayHistoryResponse(BaseModel):
    entries: list[PlayHistoryEntry]
    total: int
    page: int
    limit: int


class UpcomingScheduleEntry(BaseModel):
    estimated_date: str
    station_name: str
    time_slot: str
    asset_title: str


class SponsorStats(BaseModel):
    total_plays_month: int
    total_plays_alltime: int
    next_scheduled: str | None = None
