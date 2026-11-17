import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Limits match the song_requests columns (models/song_request.py); longer text used
# to reach the database and fail there (500). The message column is TEXT: 1000 is ours.
REQUESTER_NAME_MAX = 255
SONG_TITLE_MAX = 500
SONG_ARTIST_MAX = 255
REQUESTER_MESSAGE_MAX = 1000


class SongRequestCreate(BaseModel):
    station_id: str
    requester_name: str = Field(min_length=1, max_length=REQUESTER_NAME_MAX)
    song_title: str = Field(min_length=1, max_length=SONG_TITLE_MAX)
    song_artist: str | None = Field(default=None, max_length=SONG_ARTIST_MAX)
    requester_message: str | None = Field(default=None, max_length=REQUESTER_MESSAGE_MAX)

    @field_validator("requester_name", "song_title", "song_artist", "requester_message", mode="before")
    @classmethod
    def _strip(cls, value):
        # Blank name or title -> 422 (min_length); blank artist or message -> not given
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("song_artist", "requester_message")
    @classmethod
    def _blank_is_none(cls, value):
        return value or None


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
