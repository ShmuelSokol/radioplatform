import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SongRequestCreate(BaseModel):
    station_id: str
    requester_name: str
    song_title: str
    song_artist: str | None = None
    requester_message: str | None = None


class SongRequestUpdate(BaseModel):
    status: str | None = None
    asset_id: str | None = None


class SongRequestInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str
    requester_name: str
    song_title: str
    song_artist: str | None = None
    requester_message: str | None = None
    asset_id: uuid.UUID | str | None = None
    status: str
    reviewed_by: uuid.UUID | str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SongRequestListResponse(BaseModel):
    requests: list[SongRequestInDB]
    total: int
