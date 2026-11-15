"""Schemas for sponsorship spot recordings (script -> audio -> approval)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SpotRecordingCreate(BaseModel):
    sponsor_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    script_text: str = Field(..., min_length=3, max_length=5000)


class SpotRecordingUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    script_text: str | None = Field(None, min_length=3, max_length=5000)
    notes: str | None = Field(None, max_length=2000)


class SpotRecordingOut(BaseModel):
    id: UUID | str
    sponsor_id: UUID | str
    title: str
    script_text: str
    status: str
    voice: str | None = None
    has_audio: bool = False
    duration: float | None = None
    review: dict | None = None
    notes: str | None = None
    asset_id: UUID | str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
