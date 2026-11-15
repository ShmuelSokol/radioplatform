"""Pydantic schemas for program blocks (named runs of items at clock times)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.spot_schedule import _clean_days
from app.services.spot_schedule_service import parse_fixed_times


class BlockItem(BaseModel):
    kind: Literal["asset", "category", "time", "weather"]
    asset_id: UUID | None = None
    asset_type: str | None = Field(None, max_length=50)
    category: str | None = Field(None, max_length=100)
    label: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def _complete(self):
        if self.kind == "asset" and self.asset_id is None:
            raise ValueError("choose the recording for this item")
        if self.kind == "category" and not (self.asset_type or "").strip():
            raise ValueError("choose the type of recording for this item")
        return self


def _clean_block_times(value: str) -> str:
    times = parse_fixed_times(value)
    if not times:
        raise ValueError("add at least one time of day")
    if len(times) > 96:
        raise ValueError("at most 96 times a day")
    return ",".join(f"{h:02d}:{m:02d}" for h, m in times)


class ProgramBlockBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True
    days_of_week: str = "0,1,2,3,4,5,6"
    times: str
    timing_mode: Literal["hard", "soft"] = "hard"
    items: list[BlockItem] = Field(..., min_length=1, max_length=20)

    _days = field_validator("days_of_week")(_clean_days)
    _times = field_validator("times")(_clean_block_times)


class ProgramBlockCreate(ProgramBlockBase):
    station_id: UUID


class ProgramBlockUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None
    days_of_week: str | None = None
    times: str | None = None
    timing_mode: Literal["hard", "soft"] | None = None
    items: list[BlockItem] | None = Field(None, min_length=1, max_length=20)

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
    items: list[dict]
    created_at: datetime | None = None


class PreviewItem(BaseModel):
    kind: str
    label: str
    title: str | None = None
    duration: float | None = None
    problem: str | None = None


class PreviewOccurrence(BaseModel):
    block_id: UUID | str
    block_name: str
    at: datetime
    local_time: str  # "Mon 16 Nov 3:00 PM" in station time
    timing_mode: str
    seconds: float
    items: list[PreviewItem]
    problems: list[str]


class BlockPreview(BaseModel):
    timezone: str
    hours: int
    occurrences: list[PreviewOccurrence]
