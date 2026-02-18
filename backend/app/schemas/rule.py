import uuid

from pydantic import BaseModel, ConfigDict


class RuleCreate(BaseModel):
    name: str
    description: str | None = None
    rule_type: str = "rotation"
    asset_type: str = "music"
    category: str | None = None
    hour_start: int = 0
    hour_end: int = 24
    days_of_week: str = "0,1,2,3,4,5,6"
    interval_minutes: int | None = None
    songs_between: int | None = None
    priority: int = 10
    is_active: bool = True
    constraints: dict | None = None


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    rule_type: str | None = None
    asset_type: str | None = None
    category: str | None = None
    hour_start: int | None = None
    hour_end: int | None = None
    days_of_week: str | None = None
    interval_minutes: int | None = None
    songs_between: int | None = None
    priority: int | None = None
    is_active: bool | None = None
    constraints: dict | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    name: str
    description: str | None = None
    rule_type: str
    asset_type: str
    category: str | None = None
    hour_start: int
    hour_end: int
    days_of_week: str
    interval_minutes: int | None = None
    songs_between: int | None = None
    priority: int
    is_active: bool
    constraints: dict | None = None


class RuleListResponse(BaseModel):
    rules: list[RuleOut]
    total: int


class ScheduleSlot(BaseModel):
    time: str
    asset_type: str
    category: str | None = None
    rule_name: str
    duration_minutes: float | None = None


class SchedulePreview(BaseModel):
    date: str
    slots: list[ScheduleSlot]
    total_hours: float
