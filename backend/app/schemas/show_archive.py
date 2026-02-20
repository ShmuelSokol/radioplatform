import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ShowArchiveCreate(BaseModel):
    station_id: str
    title: str
    description: str | None = None
    host_name: str | None = None
    recorded_at: datetime | None = None
    duration_seconds: int | None = None
    audio_url: str
    cover_image_url: str | None = None
    live_show_id: str | None = None


class ShowArchiveUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    host_name: str | None = None
    audio_url: str | None = None
    cover_image_url: str | None = None
    is_published: bool | None = None


class ShowArchiveInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    station_id: uuid.UUID | str
    title: str
    description: str | None = None
    host_name: str | None = None
    recorded_at: datetime | None = None
    duration_seconds: int | None = None
    audio_url: str
    cover_image_url: str | None = None
    is_published: bool
    download_count: int
    live_show_id: uuid.UUID | str | None = None
    created_at: datetime
    updated_at: datetime


class ShowArchiveListResponse(BaseModel):
    archives: list[ShowArchiveInDB]
    total: int
