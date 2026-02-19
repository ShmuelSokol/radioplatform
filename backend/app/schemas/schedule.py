"""
Pydantic schemas for Schedule, ScheduleBlock, PlaylistEntry, NowPlaying.
"""
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.playlist_entry import PlaybackMode
from app.models.schedule_block import DayOfWeek, RecurrenceType, SunEvent


# ==================== Schedule ====================
class ScheduleBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = None
    is_active: bool = True
    priority: int = 0


class ScheduleCreate(ScheduleBase):
    station_id: UUID | str


class ScheduleUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    priority: int | None = None


class ScheduleInDB(ScheduleBase):
    id: UUID | str
    station_id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Schedule(ScheduleInDB):
    """Full schedule response with blocks"""
    blocks: list["ScheduleBlock"] = []


# ==================== ScheduleBlock ====================
class ScheduleBlockBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: str | None = None
    start_time: time
    end_time: time
    recurrence_type: RecurrenceType = RecurrenceType.DAILY
    recurrence_pattern: list[Any] | None = None  # List of days or day numbers
    priority: int = 0
    playback_mode: PlaybackMode = PlaybackMode.SEQUENTIAL
    start_date: date | None = None  # For ONE_TIME recurrence
    end_date: date | None = None  # For ONE_TIME recurrence
    start_sun_event: SunEvent | None = None
    start_sun_offset: int | None = None  # minutes offset from sun event
    end_sun_event: SunEvent | None = None
    end_sun_offset: int | None = None


class ScheduleBlockCreate(ScheduleBlockBase):
    schedule_id: UUID | str


class ScheduleBlockUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    recurrence_type: RecurrenceType | None = None
    recurrence_pattern: list[Any] | None = None
    priority: int | None = None
    playback_mode: PlaybackMode | None = None
    start_date: date | None = None
    end_date: date | None = None
    start_sun_event: SunEvent | None = None
    start_sun_offset: int | None = None
    end_sun_event: SunEvent | None = None
    end_sun_offset: int | None = None


class ScheduleBlockInDB(ScheduleBlockBase):
    id: UUID | str
    schedule_id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScheduleBlock(ScheduleBlockInDB):
    """Full block response with playlist entries"""
    playlist_entries: list["PlaylistEntry"] = []


# ==================== PlaylistEntry ====================
class PlaylistEntryBase(BaseModel):
    asset_id: UUID | str
    position: int = 0
    weight: int = 1
    playback_mode: PlaybackMode = PlaybackMode.SEQUENTIAL
    is_enabled: bool = True
    playback_config: dict[str, Any] | None = None


class PlaylistEntryCreate(PlaylistEntryBase):
    block_id: UUID | str


class PlaylistEntryUpdate(BaseModel):
    asset_id: UUID | str | None = None
    position: int | None = None
    weight: int | None = None
    playback_mode: PlaybackMode | None = None
    is_enabled: bool | None = None
    playback_config: dict[str, Any] | None = None


class PlaylistEntryInDB(PlaylistEntryBase):
    id: UUID | str
    block_id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlaylistEntry(PlaylistEntryInDB):
    """Full playlist entry response (can include asset details)"""
    pass


# ==================== NowPlaying ====================
class NowPlayingBase(BaseModel):
    station_id: UUID | str
    asset_id: UUID | str | None = None
    started_at: datetime
    ends_at: datetime | None = None
    block_id: UUID | str | None = None
    listener_count: int = 0
    stream_url: str | None = None


class NowPlayingCreate(NowPlayingBase):
    pass


class NowPlayingUpdate(BaseModel):
    asset_id: UUID | str | None = None
    started_at: datetime | None = None
    ends_at: datetime | None = None
    block_id: UUID | str | None = None
    listener_count: int | None = None
    stream_url: str | None = None


class NowPlayingInDB(NowPlayingBase):
    id: UUID | str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NowPlaying(NowPlayingInDB):
    """Full now-playing response (can include asset/station details)"""
    pass


# Forward reference resolution
Schedule.model_rebuild()
ScheduleBlock.model_rebuild()
