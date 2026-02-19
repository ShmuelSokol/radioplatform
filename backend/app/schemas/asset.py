import uuid

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


class TranscodeRequest(BaseModel):
    codec: str = "aac"
    bitrate: str = "128k"


class ClipRequest(BaseModel):
    start: float
    duration: float


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
