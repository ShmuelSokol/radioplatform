"""Pydantic schemas for sponsor spot schedules."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.spot_schedule_service import DAYPARTS


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


class SpotScheduleBase(BaseModel):
    name: str | None = None
    station_id: UUID | None = None
    days_of_week: str = "0,1,2,3,4,5,6"
    dayparts: str = "morning,afternoon,evening"
    plays_per_daypart: int = Field(1, ge=1, le=12)
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool = True

    _days = field_validator("days_of_week")(_clean_days)
    _parts = field_validator("dayparts")(_clean_dayparts)


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


class SpotPlanPreview(BaseModel):
    station_id: UUID | str
    timezone: str
    days: int
    planned: list[PlannedSpot]
    unplaced: list[UnplacedSpot]
