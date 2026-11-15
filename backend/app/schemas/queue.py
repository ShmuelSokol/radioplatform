import uuid
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    # Sent by the client per tap: a repeat of the same id within a few seconds is ignored,
    # so a double tap on an instant button cannot play the item twice.
    client_request_id: str | None = Field(default=None, max_length=100)


class QueueLoop(BaseModel):
    """Loop the item on air (it plays again each time it ends) until switched off or Next."""

    on: bool


class QueueInsert(BaseModel):
    """Insert one or more assets, in order, right after `after_entry_id`
    (null = at the top, so they play next)."""

    asset_id: uuid.UUID | None = None
    asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    after_entry_id: uuid.UUID | None = None
