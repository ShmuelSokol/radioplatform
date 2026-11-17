"""Pydantic schemas for sponsor spot schedules."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.spot_schedule_service import DAYPARTS, TIMING_MODES, parse_fixed_times


def _clean_days(value: str) -> str:
    days = sorted({int(d) for d in value.split(",") if d.strip() != ""})
    if not days or any(d < 0 or d > 6 for d in days):
        raise ValueError("days_of_week must be comma-separated numbers 0 (Mon) to 6 (Sun)")
    return ",".join(str(d) for d in days)


def _clean_dayparts(value: str) -> str:
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    if not parts or any(p not in DAYPARTS for p in parts):
        raise ValueError(f"dayparts must be chosen from: {', '.join(DAYPARTS)}")
    return ",".join(p for p in DAYPARTS if p in parts)


def _clean_times(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    times = parse_fixed_times(value)
    if len(times) > 48:
        raise ValueError("at most 48 exact times a day")
    return ",".join(f"{h:02d}:{m:02d}" for h, m in times)


def _clean_timing(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in TIMING_MODES:
        raise ValueError("timing_mode must be hard or soft")
    return value


class SpotScheduleBase(BaseModel):
    name: str | None = None
    station_id: UUID | None = None
    days_of_week: str = "0,1,2,3,4,5,6"
    dayparts: str = "morning,afternoon,evening"
    plays_per_daypart: int = Field(1, ge=1, le=12)
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool = True
    # Exact station-time clock times ("07:00,13:30"); replaces the daypart spread
    fixed_times: str | None = None
    # hard | soft | None (the station's default for that slot)
    timing_mode: str | None = None

    _days = field_validator("days_of_week")(_clean_days)
    _parts = field_validator("dayparts")(_clean_dayparts)
    _times = field_validator("fixed_times")(_clean_times)
    _timing = field_validator("timing_mode")(_clean_timing)


class SpotScheduleCreate(SpotScheduleBase):
    sponsor_id: UUID
    asset_id: UUID

    @model_validator(mode="after")
    def _dates_in_order(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


# May be left out of an update, but cannot be cleared.
_NOT_NULLABLE = ("asset_id", "days_of_week", "dayparts", "plays_per_daypart", "is_active")


class SpotScheduleUpdate(BaseModel):
    name: str | None = None
    asset_id: UUID | None = None
    station_id: UUID | None = None
    days_of_week: str | None = None
    dayparts: str | None = None
    plays_per_daypart: int | None = Field(None, ge=1, le=12)
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool | None = None
    fixed_times: str | None = None
    timing_mode: str | None = None

    _times = field_validator("fixed_times")(_clean_times)
    _timing = field_validator("timing_mode")(_clean_timing)

    @model_validator(mode="after")
    def _no_null_required(self):
        nulls = [
            f for f in _NOT_NULLABLE if f in self.model_fields_set and getattr(self, f) is None
        ]
        if nulls:
            raise ValueError(f"{', '.join(nulls)} cannot be null")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v):
        return _clean_days(v) if v is not None else v

    @field_validator("dayparts")
    @classmethod
    def _parts(cls, v):
        return _clean_dayparts(v) if v is not None else v


class SpotScheduleOut(SpotScheduleBase):
    id: UUID | str
    sponsor_id: UUID | str
    asset_id: UUID | str
    station_id: UUID | str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlannedSpot(BaseModel):
    scheduled_at: datetime
    local_time: str
    daypart: str
    timing_mode: str = "soft"
    fixed: bool = False
    block: str | None = None  # the program block whose sponsor place this is
    schedule_id: UUID | str
    sponsor_id: UUID | str
    sponsor_name: str
    asset_id: UUID | str
    asset_title: str


class UnplacedSpot(BaseModel):
    local_date: str
    daypart: str
    schedule_id: UUID | str
    sponsor_name: str
    reason: str
    count: int = 1  # plays with the same sponsorship, day, part of the day and reason


class OffAirWindow(BaseModel):
    """A blackout inside the look-ahead and how many plays it set aside."""

    name: str
    start: datetime
    end: datetime
    skipped_plays: int


class SpotPlanPreview(BaseModel):
    station_id: UUID | str
    timezone: str
    days: int
    planned: list[PlannedSpot]
    unplaced: list[UnplacedSpot]
    off_air: list[OffAirWindow] = []
    # Spots mode is off in the control room: nothing is planned until it is on again
    spots_off: bool = False
