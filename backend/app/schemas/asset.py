from pydantic import BaseModel


class AssetResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    title: str
    artist: str | None = None
    album: str | None = None
    duration: float | None = None
    file_path: str
    album_art_path: str | None = None
    metadata_extra: dict | None = None
    created_by: str | None = None


class AssetListResponse(BaseModel):
    assets: list[AssetResponse]
    total: int


class TranscodeRequest(BaseModel):
    codec: str = "aac"
    bitrate: str = "128k"


class ClipRequest(BaseModel):
    start: float
    duration: float


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
