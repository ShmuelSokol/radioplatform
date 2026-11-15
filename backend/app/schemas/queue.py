import uuid
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.asset import AssetResponse


class QueueEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str
    asset_id: uuid.UUID | str
    position: int
    status: str
    source: str | None = None
    preempt_at: datetime | None = None
    timing_mode: str | None = None
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


class QueueFireNow(BaseModel):
    """Put an asset on air now. `fade`: the music fades out and the asset starts at once.
    `after-current`: it plays when the current track ends (same as play-next)."""

    asset_id: uuid.UUID
    mode: Literal["fade", "after-current"] = "fade"
