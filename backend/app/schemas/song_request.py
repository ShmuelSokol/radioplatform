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
    station_name: str | None = None
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
    matched_asset_title: str | None = None
    matched_asset_artist: str | None = None


class SongRequestSubmitResponse(BaseModel):
    """Response for public song request submission with auto-approval info."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str
    requester_name: str
    song_title: str
    song_artist: str | None = None
    requester_message: str | None = None
    asset_id: uuid.UUID | str | None = None
    status: str
    created_at: datetime
    # Auto-approval feedback
    matched_asset_title: str | None = None
    matched_asset_artist: str | None = None
    match_confidence: float = 0.0
    auto_approved: bool = False
    queue_position: int | None = None
    songs_ahead: int | None = None
    estimated_wait_minutes: float | None = None


class SongRequestListResponse(BaseModel):
    requests: list[SongRequestInDB]
    total: int
