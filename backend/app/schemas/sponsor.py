"""Pydantic schemas for Sponsor."""
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SponsorBase(BaseModel):
    name: str
    # Left over from the first sponsor screen (one spot per sponsor). A client is
    # created without them now; their spots are their recordings and contracts.
    length_seconds: float = 30
    priority: int = 0
    audio_file_path: str = ""
    target_rules: dict[str, Any] | None = None  # e.g. {"hour_start": 6, "hour_end": 22, "max_per_hour": 4}
    insertion_policy: str = "between_tracks"  # "between_tracks", "every_n_songs", "fixed_interval"


class SponsorCreate(SponsorBase):
    user_id: UUID | str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    company_name: str | None = None
    contact_name: str | None = None
    notes: str | None = None


class SponsorUpdate(BaseModel):
    name: str | None = None
    length_seconds: float | None = None
    priority: int | None = None
    audio_file_path: str | None = None
    target_rules: dict[str, Any] | None = None
    insertion_policy: str | None = None
    user_id: UUID | str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    company_name: str | None = None
    contact_name: str | None = None
    notes: str | None = None


class SponsorInDB(SponsorBase):
    id: UUID | str
    created_at: datetime
    updated_at: datetime
    user_id: UUID | str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    company_name: str | None = None
    contact_name: str | None = None
    notes: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── client database views (services/sponsor_accounts.py) ─────────


class ClientSummaryOut(BaseModel):
    id: UUID | str
    name: str
    company_name: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    # running | upcoming | paused | ended | none (no contract yet)
    status: str
    active_from: date | None = None
    active_until: date | None = None
    open_ended: bool = False
    contracts_running: int = 0
    contracts_total: int = 0
    spots_on_air: int = 0
    spots_in_progress: int = 0
    plays_7_days: int = 0
    plays_total: int = 0
    last_played_at: datetime | None = None
    has_portal_login: bool = False
    created_at: datetime | None = None


class ClientContractOut(BaseModel):
    id: str
    name: str | None = None
    station_id: str | None = None
    station_name: str
    asset_id: str
    asset_title: str
    duration: float | None = None
    days_of_week: str
    dayparts: str
    plays_per_daypart: int
    fixed_times: str | None = None
    timing_mode: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool
    state: str  # running | upcoming | paused | ended
    problem: str | None = None
    plays_in_contract: int = 0
    plays_7_days: int = 0
    last_played_at: datetime | None = None
    created_at: datetime | None = None


class ClientSpotOut(BaseModel):
    key: str
    kind: str  # audio | recording | campaign
    stage: str
    stage_label: str
    title: str
    script: str | None = None
    duration: float | None = None
    asset_id: str | None = None
    recording_id: str | None = None
    campaign_id: str | None = None
    status_detail: str | None = None
    contract_ids: list[str] = []
    plays_total: int = 0
    last_played_at: datetime | None = None
    first_played_at: datetime | None = None
    updated_at: datetime | None = None


class StageOut(BaseModel):
    key: str
    label: str


class ClientAccountOut(BaseModel):
    client: ClientSummaryOut
    notes: str | None = None
    contracts: list[ClientContractOut]
    spots: list[ClientSpotOut]
    stages: list[StageOut]


class ClientPlayOut(BaseModel):
    id: str
    station_id: str
    station_name: str
    asset_id: str | None = None
    asset_title: str
    start_utc: datetime
    end_utc: datetime | None = None
    local_date: str
    local_time: str
    aired_seconds: float | None = None
    length_seconds: float | None = None
    how: str


class ClientPlaysOut(BaseModel):
    total: int
    page: int
    limit: int
    entries: list[ClientPlayOut]
