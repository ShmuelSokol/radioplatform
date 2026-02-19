"""Pydantic schemas for HolidayWindow."""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class HolidayWindowBase(BaseModel):
    name: str
    start_datetime: datetime
    end_datetime: datetime
    is_blackout: bool = True
    affected_stations: dict[str, Any] | None = None  # {"station_ids": [...]} or null for all
    replacement_content: str | None = None


class HolidayWindowCreate(HolidayWindowBase):
    pass


class HolidayWindowUpdate(BaseModel):
    name: str | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    is_blackout: bool | None = None
    affected_stations: dict[str, Any] | None = None
    replacement_content: str | None = None


class HolidayWindowInDB(HolidayWindowBase):
    id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
