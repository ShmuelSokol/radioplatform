from pydantic import BaseModel

from app.models.station import StationType


class StationCreate(BaseModel):
    name: str
    type: StationType = StationType.INTERNET
    timezone: str = "UTC"
    latitude: float | None = None
    longitude: float | None = None
    stream_url: str | None = None
    broadcast_config: dict | None = None
    description: str | None = None
    logo_url: str | None = None


class StationUpdate(BaseModel):
    name: str | None = None
    type: StationType | None = None
    timezone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    stream_url: str | None = None
    broadcast_config: dict | None = None
    is_active: bool | None = None
    description: str | None = None
    logo_url: str | None = None


class ChannelStreamResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    channel_name: str
    bitrate: int
    codec: str
    hls_manifest_path: str | None = None
    listeners_count: int


class StationResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    type: StationType
    timezone: str
    latitude: float | None = None
    longitude: float | None = None
    stream_url: str | None = None
    broadcast_config: dict | None = None
    is_active: bool
    description: str | None = None
    logo_url: str | None = None
    channels: list[ChannelStreamResponse] = []


class StationListResponse(BaseModel):
    stations: list[StationResponse]
    total: int
