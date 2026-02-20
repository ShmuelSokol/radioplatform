import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Registration / Login ──────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None


class RegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    name: str
    pin: str  # shown once at registration


class LoginRequest(BaseModel):
    pin: str


class TasteProfile(BaseModel):
    label: str
    description: str
    top_category: str | None = None
    stats: dict | None = None


class LoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    name: str
    taste_profile: TasteProfile
    favorites_count: int = 0
    ratings_count: int = 0


# ── Ratings ────────────────────────────────────────────────────

class RateRequest(BaseModel):
    asset_id: uuid.UUID | str
    rating: int = Field(ge=1, le=5)
    is_favorite: bool = False


class RatingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    asset_id: uuid.UUID | str
    rating: int
    is_favorite: bool
    asset_title: str | None = None
    asset_artist: str | None = None
    created_at: datetime | None = None


# ── Members (admin) ───────────────────────────────────────────

class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    name: str
    pin: str
    phone: str | None = None
    email: str | None = None
    is_active: bool = True
    taste_profile: TasteProfile | None = None
    ratings_count: int = 0
    favorites_count: int = 0
    created_at: datetime | None = None


class MemberListResponse(BaseModel):
    members: list[MemberResponse]
    total: int


# ── Song Rankings (admin) ─────────────────────────────────────

class SongRankingResponse(BaseModel):
    asset_id: uuid.UUID | str
    title: str
    artist: str | None = None
    avg_rating: float
    total_ratings: int
    favorite_count: int


# ── Raffles ────────────────────────────────────────────────────

class RaffleCreate(BaseModel):
    title: str
    description: str | None = None
    prize: str | None = None
    station_id: uuid.UUID | str | None = None
    starts_at: datetime
    ends_at: datetime


class RaffleUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    prize: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: str | None = None


class RaffleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    title: str
    description: str | None = None
    prize: str | None = None
    station_id: uuid.UUID | str | None = None
    starts_at: datetime
    ends_at: datetime
    status: str
    winner_id: uuid.UUID | str | None = None
    winner_name: str | None = None
    entry_count: int = 0
    created_at: datetime | None = None


class RaffleEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | str
    member_id: uuid.UUID | str
    member_name: str | None = None
    created_at: datetime | None = None
