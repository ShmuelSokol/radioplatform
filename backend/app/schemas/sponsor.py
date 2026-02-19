"""Pydantic schemas for Sponsor."""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SponsorBase(BaseModel):
    name: str
    length_seconds: float
    priority: int = 0
    audio_file_path: str
    target_rules: dict[str, Any] | None = None  # e.g. {"hour_start": 6, "hour_end": 22, "max_per_hour": 4}
    insertion_policy: str = "between_tracks"  # "between_tracks", "every_n_songs", "fixed_interval"


class SponsorCreate(SponsorBase):
    pass


class SponsorUpdate(BaseModel):
    name: str | None = None
    length_seconds: float | None = None
    priority: int | None = None
    audio_file_path: str | None = None
    target_rules: dict[str, Any] | None = None
    insertion_policy: str | None = None


class SponsorInDB(SponsorBase):
    id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
