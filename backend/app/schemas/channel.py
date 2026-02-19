"""Pydantic schemas for ChannelStream."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChannelBase(BaseModel):
    channel_name: str
    bitrate: int = 128
    codec: str = "aac"
    is_active: bool = True
    schedule_id: UUID | str | None = None


class ChannelCreate(ChannelBase):
    station_id: UUID | str


class ChannelUpdate(BaseModel):
    channel_name: str | None = None
    bitrate: int | None = None
    codec: str | None = None
    is_active: bool | None = None
    schedule_id: UUID | str | None = None


class ChannelInDB(ChannelBase):
    id: UUID | str
    station_id: UUID | str
    hls_manifest_path: str | None = None
    listeners_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
