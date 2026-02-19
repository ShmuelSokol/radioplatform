"""Pydantic schemas for Category."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CategoryCreate(BaseModel):
    name: str


class CategoryUpdate(BaseModel):
    name: str | None = None


class CategoryInDB(BaseModel):
    id: UUID | str
    name: str
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
