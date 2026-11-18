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
    scheduled_at: datetime | None = None


class SponsorStats(BaseModel):
    total_plays_month: int
    total_plays_alltime: int
    next_scheduled: str | None = None
    next_scheduled_at: datetime | None = None
    last_played: datetime | None = None


class SponsorSpot(BaseModel):
    """One of the sponsor's spots, with when it last aired and airs next."""
    asset_id: UUID | str
    title: str
    duration_seconds: float | None = None
    last_played_at: datetime | None = None
    next_play_at: datetime | None = None
    plays_month: int = 0
    plays_alltime: int = 0
    scheduled: bool = False


class MissedPlay(BaseModel):
    """One time of day where the sponsor got fewer plays than promised, or a play was moved."""
    when: str  # "past" (did not air) or "coming" (will not fit / moved)
    date: str  # station-local YYYY-MM-DD
    daypart: str  # morning, afternoon, evening, overnight
    station_name: str
    asset_title: str
    promised: int
    played: int
    missed: int
    moved: int = 0
    note: str = ""


class MissedPlaysResponse(BaseModel):
    days: int  # how far back the past rows look
    missed_past: int
    missed_coming: int
    moved_coming: int
    rows: list[MissedPlay]
