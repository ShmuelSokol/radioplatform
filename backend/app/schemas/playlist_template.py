"""Pydantic schemas for PlaylistTemplate and TemplateSlot."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ==================== TemplateSlot ====================
class TemplateSlotBase(BaseModel):
    position: int = 0
    asset_type: str = Field(..., max_length=50)
    category: str | None = None


class TemplateSlotCreate(TemplateSlotBase):
    template_id: UUID


class TemplateSlotUpdate(BaseModel):
    position: int | None = None
    asset_type: str | None = Field(None, max_length=50)
    category: str | None = None


class TemplateSlotInDB(TemplateSlotBase):
    id: UUID | str
    template_id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ==================== PlaylistTemplate ====================
class PlaylistTemplateCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = None
    station_id: UUID | str | None = None
    is_active: bool = True
    slots: list[TemplateSlotBase] | None = None  # inline slot creation


class PlaylistTemplateUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    station_id: UUID | str | None = None
    is_active: bool | None = None


class PlaylistTemplateInDB(BaseModel):
    id: UUID | str
    name: str
    description: str | None = None
    station_id: UUID | str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    slots: list[TemplateSlotInDB] = []

    model_config = ConfigDict(from_attributes=True)
