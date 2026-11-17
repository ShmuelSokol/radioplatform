"""Pydantic schemas for HolidayWindow."""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _all_stations_if_no_list(v):
    """{"station_ids": null} or {} means every station: store it as null, the
    one form every blackout check reads as "all stations"."""
    if isinstance(v, dict) and v.get("station_ids") is None:
        return None
    return v


class HolidayWindowBase(BaseModel):
    name: str
    start_datetime: datetime
    end_datetime: datetime
    is_blackout: bool = True
    affected_stations: dict[str, Any] | None = None  # {"station_ids": [...]} or null for all
    replacement_content: str | None = None
    reason: str | None = None

    @field_validator("affected_stations")
    @classmethod
    def _norm_affected(cls, v):
        return _all_stations_if_no_list(v)


class HolidayWindowCreate(HolidayWindowBase):
    pass


class HolidayWindowUpdate(BaseModel):
    name: str | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    is_blackout: bool | None = None
    affected_stations: dict[str, Any] | None = None
    replacement_content: str | None = None
    reason: str | None = None

    @field_validator("affected_stations")
    @classmethod
    def _norm_affected(cls, v):
        return _all_stations_if_no_list(v)


class HolidayWindowInDB(HolidayWindowBase):
    id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HolidayListResponse(BaseModel):
    holidays: list[HolidayWindowInDB]
    total: int


class AutoGenerateRequest(BaseModel):
    station_id: UUID | str
    months_ahead: int = Field(default=12, ge=1, le=24)


class AutoGenerateResponse(BaseModel):
    created: int
    skipped: int
