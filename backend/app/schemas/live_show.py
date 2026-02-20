import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


# --- LiveShow schemas ---

class LiveShowCreate(BaseModel):
    station_id: uuid.UUID | str
    title: str
    description: str | None = None
    broadcast_mode: str = "webrtc"
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    calls_enabled: bool = True


class LiveShowUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    broadcast_mode: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    calls_enabled: bool | None = None


class LiveShowInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str
    host_user_id: uuid.UUID | str | None = None
    title: str
    description: str | None = None
    status: str
    broadcast_mode: str
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    twilio_conference_sid: str | None = None
    icecast_mount: str | None = None
    calls_enabled: bool
    created_at: datetime
    updated_at: datetime


class LiveShowListResponse(BaseModel):
    shows: list[LiveShowInDB]
    total: int


# --- CallInRequest schemas ---

class CallInRequestInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    live_show_id: uuid.UUID | str
    caller_phone: str
    caller_name: str | None = None
    status: str
    twilio_call_sid: str | None = None
    hold_start: datetime | None = None
    air_start: datetime | None = None
    air_end: datetime | None = None
    notes: str | None = None
    screened_by: uuid.UUID | str | None = None
    created_at: datetime
    updated_at: datetime


class CallInRequestListResponse(BaseModel):
    calls: list[CallInRequestInDB]
    total: int


class ScreenerAction(BaseModel):
    caller_name: str | None = None
    notes: str | None = None
