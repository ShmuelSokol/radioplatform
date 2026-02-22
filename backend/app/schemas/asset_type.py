"""Pydantic schemas for AssetType."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AssetTypeCreate(BaseModel):
    name: str


class AssetTypeUpdate(BaseModel):
    name: str | None = None


class AssetTypeInDB(BaseModel):
    id: UUID | str
    name: str
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
