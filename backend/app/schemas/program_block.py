"""Pydantic schemas for program blocks (named runs of items at clock times)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.spot_schedule import _clean_days
from app.services.spot_schedule_service import parse_fixed_times


class BlockItem(BaseModel):
    kind: Literal["asset", "category", "time", "weather", "sponsors"]
    asset_id: UUID | None = None
    asset_type: str | None = Field(None, max_length=50)
    category: str | None = Field(None, max_length=100)
    label: str | None = Field(None, max_length=80)
    count: int | None = Field(None, ge=1, le=10)  # sponsors: how many places
    # sponsors: the most sponsorship time this item may hold in one airing
    max_seconds: int | None = Field(None, ge=15, le=900)
    # asset/category: when it has nothing to play the block simply goes without it;
    # the block's programme (its last recording that is not optional) decides
    # whether the recordings before it play
    optional: bool = False

    @model_validator(mode="after")
    def _complete(self):
        if self.kind == "asset" and self.asset_id is None:
            raise ValueError("choose the recording for this item")
        if self.kind == "category" and not (self.asset_type or "").strip():
            raise ValueError("choose the type of recording for this item")
        if self.kind != "sponsors":
            self.max_seconds = None
        if self.kind == "sponsors" and self.count is None and self.max_seconds is None:
            self.count = 1
        return self


def _clean_block_times(value: str) -> str:
    times = parse_fixed_times(value)
    if not times:
        raise ValueError("add at least one time of day")
    if len(times) > 96:
        raise ValueError("at most 96 times a day")
    return ",".join(f"{h:02d}:{m:02d}" for h, m in times)


def _clean_season_day(value: str | None) -> str | None:
    """ "MM-DD" (also "M-D" or "MM/DD"), checked against a leap year so 02-29 is allowed."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("/", "-")
    try:
        month, day = (int(x) for x in raw.split("-"))
        date(2000, month, day)
    except (ValueError, TypeError):
        raise ValueError("use month-day, like 05-15") from None
    return f"{month:02d}-{day:02d}"


def _check_season(start: str | None, end: str | None) -> None:
    if bool(start) != bool(end):
        raise ValueError("give both the first and the last day of the season, or neither")


class ProgramBlockBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True
    days_of_week: str = "0,1,2,3,4,5,6"
    times: str
    timing_mode: Literal["hard", "soft"] = "hard"
    latest_minutes: int | None = Field(None, ge=1, le=60)
    items: list[BlockItem] = Field(..., min_length=1, max_length=20)
    # Every year from season_start to season_end ("MM-DD"); both empty = all year
    season_start: str | None = None
    season_end: str | None = None

    _days = field_validator("days_of_week")(_clean_days)
    _times = field_validator("times")(_clean_block_times)
    _season = field_validator("season_start", "season_end")(_clean_season_day)

    @model_validator(mode="after")
    def _season_pair(self):
        _check_season(self.season_start, self.season_end)
        return self


class ProgramBlockCreate(ProgramBlockBase):
    station_id: UUID


class ProgramBlockUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None
    days_of_week: str | None = None
    times: str | None = None
    timing_mode: Literal["hard", "soft"] | None = None
    latest_minutes: int | None = Field(None, ge=1, le=60)
    items: list[BlockItem] | None = Field(None, min_length=1, max_length=20)
    # null clears (all year); the pair is checked against the stored block on save
    season_start: str | None = None
    season_end: str | None = None

    _season = field_validator("season_start", "season_end")(_clean_season_day)

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v):
        return None if v is None else _clean_days(v)

    @field_validator("times")
    @classmethod
    def _times(cls, v):
        return None if v is None else _clean_block_times(v)


class ProgramBlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | str
    station_id: UUID | str
    name: str
    is_active: bool
    days_of_week: str
    times: str
    timing_mode: str
    latest_minutes: int | None = None
    items: list[dict]
    season_start: str | None = None
    season_end: str | None = None
    created_at: datetime | None = None


class PreviewItem(BaseModel):
    kind: str
    label: str
    title: str | None = None
    duration: float | None = None
    problem: str | None = None
    open_place: bool = False


class PreviewOccurrence(BaseModel):
    block_id: UUID | str
    block_name: str
    at: datetime
    local_time: str  # "Mon 16 Nov 3:00 PM" in station time
    timing_mode: str
    latest_minutes: int | None = None
    seconds: float
    items: list[PreviewItem]
    problems: list[str]


class BlockPreview(BaseModel):
    timezone: str
    hours: int
    occurrences: list[PreviewOccurrence]
