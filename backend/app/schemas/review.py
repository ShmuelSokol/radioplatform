import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.asset import AssetResponse


class ReviewQueueCreate(BaseModel):
    name: str
    description: str | None = None
    asset_ids: list[str]


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    name: str
    description: str | None = None
    status: str
    created_by: uuid.UUID | str | None = None
    total_items: int
    reviewed_items: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReviewQueueListResponse(BaseModel):
    queues: list[ReviewQueueResponse]
    total: int


class ReviewItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    queue_id: uuid.UUID | str
    asset_id: uuid.UUID | str
    position: int
    status: str
    assigned_to: uuid.UUID | str | None = None
    notes: str | None = None
    version: int
    asset: AssetResponse | None = None


class ReviewItemListResponse(BaseModel):
    items: list[ReviewItemResponse]
    total: int


class ReviewItemUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    version: int  # optimistic lock


class BatchUpdateRequest(BaseModel):
    item_ids: list[str]
    status: str
