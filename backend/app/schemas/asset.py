import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    title: str
    artist: str | None = None
    album: str | None = None
    duration: float | None = None
    file_path: str
    album_art_path: str | None = None
    metadata_extra: dict | None = None
    created_by: uuid.UUID | str | None = None
    asset_type: str = "music"
    category: str | None = None
    created_at: datetime | None = None
    last_played_at: datetime | None = None
    release_date: str | None = None
    sponsor_id: uuid.UUID | str | None = None
    sponsor_name: str | None = None


class AssetListResponse(BaseModel):
    assets: list[AssetResponse]
    total: int


class AssetUpdate(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration: float | None = None
    asset_type: str | None = None
    category: str | None = None
    file_path: str | None = None
    release_date: str | None = None
    sponsor_id: uuid.UUID | str | None = None


class TranscodeRequest(BaseModel):
    codec: str = "aac"
    bitrate: str = "128k"


class ClipRequest(BaseModel):
    start: float
    duration: float


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str


class BulkCategoryRequest(BaseModel):
    asset_ids: list[uuid.UUID | str] | None = None
    # Filter-based selection (alternative to explicit IDs):
    asset_type: str | None = None
    category_filter: str | None = None
    title_search: str | None = None
    artist_search: str | None = None
    album_search: str | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    # Target category:
    category: str


class BulkAutoTrimRequest(BaseModel):
    asset_ids: list[str] | None = None
    # Filter-based selection:
    asset_type: str | None = None
    category: str | None = None
    title_search: str | None = None
    artist_search: str | None = None
    album_search: str | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    threshold_db: float = -35
    min_silence: float = 0.3


class BulkAutoTrimStatusResponse(BaseModel):
    job_id: str
    status: str  # running, completed, failed
    total: int
    processed: int
    trimmed: int
    skipped: int
    errors: int
