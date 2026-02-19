"""
Now-playing endpoints — query current playback state, resolve active blocks.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.user import User
from app.schemas.schedule import NowPlaying, NowPlayingUpdate
from app.services.scheduling import SchedulingService

router = APIRouter(prefix="/now-playing", tags=["now-playing"])


@router.get("/{station_id}", response_model=Optional[NowPlaying])
async def get_now_playing(
    station_id: UUID | str,
    db: AsyncSession = Depends(get_db),
):
    """Get current now-playing state for a station."""
    service = SchedulingService(db)
    record = await service.get_now_playing(station_id)
    return record


@router.get("/{station_id}/active-block")
async def get_active_block(
    station_id: UUID | str,
    at_time: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get the currently active schedule block for a station.
    Useful for debugging or manual override.
    """
    service = SchedulingService(db)
    block = await service.get_active_block_for_station(station_id, at_time)
    if not block:
        return {"message": "No active block found", "station_id": str(station_id)}
    return {
        "block_id": str(block.id),
        "name": block.name,
        "start_time": str(block.start_time),
        "end_time": str(block.end_time),
        "recurrence_type": block.recurrence_type,
    }


@router.patch("/{station_id}", response_model=NowPlaying)
async def update_now_playing(
    station_id: UUID | str,
    data: NowPlayingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """
    Manually update now-playing state (e.g., for testing or manual control).
    """
    service = SchedulingService(db)
    duration = None
    if data.ends_at and data.started_at:
        duration = (data.ends_at - data.started_at).total_seconds()

    record = await service.update_now_playing(
        station_id=station_id,
        asset_id=data.asset_id,
        block_id=data.block_id,
        duration_seconds=duration,
    )
    return record


@router.delete("/{station_id}", status_code=204)
async def clear_now_playing(
    station_id: UUID | str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Clear now-playing state for a station."""
    service = SchedulingService(db)
    await service.clear_now_playing(station_id)
