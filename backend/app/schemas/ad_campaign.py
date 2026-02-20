"""Pydantic schemas for Ad Campaigns, Drafts, and Comments."""
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# --- Campaign ---

class CampaignCreate(BaseModel):
    name: str
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget_cents: int | None = None
    target_rules: dict[str, Any] | None = None


class CampaignUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget_cents: int | None = None
    target_rules: dict[str, Any] | None = None


class CampaignStatusUpdate(BaseModel):
    status: str  # one of CampaignStatus values


class CampaignInDB(BaseModel):
    id: UUID | str
    sponsor_id: UUID | str
    name: str
    description: str | None = None
    status: str
    start_date: date | None = None
    end_date: date | None = None
    budget_cents: int | None = None
    target_rules: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Draft ---

class DraftCreate(BaseModel):
    script_text: str | None = None
    notes: str | None = None


class DraftInDB(BaseModel):
    id: UUID | str
    campaign_id: UUID | str
    version: int
    script_text: str | None = None
    audio_file_path: str | None = None
    notes: str | None = None
    created_by: UUID | str | None = None
    created_at: datetime
    updated_at: datetime
    user_email: str | None = None
    user_display_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Comment ---

class CommentCreate(BaseModel):
    body: str
    draft_id: UUID | str | None = None


class CommentInDB(BaseModel):
    id: UUID | str
    campaign_id: UUID | str
    draft_id: UUID | str | None = None
    user_id: UUID | str
    body: str
    created_at: datetime
    user_email: str | None = None
    user_display_name: str | None = None

    model_config = ConfigDict(from_attributes=True)
