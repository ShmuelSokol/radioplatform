import uuid

from pydantic import BaseModel, ConfigDict

from app.schemas.asset import AssetResponse


class QueueEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str
    asset_id: uuid.UUID | str
    position: int
    status: str
    asset: AssetResponse | None = None


class QueueListResponse(BaseModel):
    entries: list[QueueEntryOut]
    total: int
    now_playing: QueueEntryOut | None = None


class QueueAdd(BaseModel):
    asset_id: uuid.UUID


class QueueReorder(BaseModel):
    entry_id: uuid.UUID
    new_position: int


class QueueDndReorder(BaseModel):
    entry_id: uuid.UUID
    new_position: int


class QueueBulkAdd(BaseModel):
    asset_ids: list[uuid.UUID]
