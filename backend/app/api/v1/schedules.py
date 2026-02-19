"""
Schedule management endpoints — CRUD for schedules, blocks, and playlist entries.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, require_manager
from app.models.playlist_entry import PlaylistEntry as PlaylistEntryModel
from app.models.schedule import Schedule as ScheduleModel
from app.models.schedule_block import ScheduleBlock as ScheduleBlockModel
from app.models.user import User
from app.schemas.schedule import (
    PlaylistEntry,
    PlaylistEntryCreate,
    PlaylistEntryUpdate,
    Schedule,
    ScheduleBlock,
    ScheduleBlockCreate,
    ScheduleBlockUpdate,
    ScheduleCreate,
    ScheduleUpdate,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


# ==================== Schedules ====================
@router.post("/", response_model=Schedule, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    data: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Create a new schedule for a station."""
    schedule = ScheduleModel(**data.model_dump())
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.get("/", response_model=List[Schedule])
async def list_schedules(
    station_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all schedules, optionally filtered by station."""
    stmt = select(ScheduleModel).options(selectinload(ScheduleModel.blocks)).offset(skip).limit(limit)
    if station_id:
        stmt = stmt.where(ScheduleModel.station_id == station_id)
    result = await db.execute(stmt)
    return result.scalars().all()


# ==================== Schedule Blocks (before /{schedule_id} to avoid route conflict) ====================
@router.post("/blocks", response_model=ScheduleBlock, status_code=status.HTTP_201_CREATED)
async def create_schedule_block(
    data: ScheduleBlockCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Create a new schedule block."""
    block = ScheduleBlockModel(**data.model_dump())
    db.add(block)
    await db.commit()
    await db.refresh(block)
    return block


@router.get("/blocks", response_model=List[ScheduleBlock])
async def list_schedule_blocks(
    schedule_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all schedule blocks, optionally filtered by schedule."""
    stmt = (
        select(ScheduleBlockModel)
        .options(selectinload(ScheduleBlockModel.playlist_entries))
        .offset(skip)
        .limit(limit)
    )
    if schedule_id:
        stmt = stmt.where(ScheduleBlockModel.schedule_id == schedule_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/blocks/{block_id}", response_model=ScheduleBlock)
async def get_schedule_block(
    block_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single schedule block by ID."""
    stmt = (
        select(ScheduleBlockModel)
        .where(ScheduleBlockModel.id == block_id)
        .options(selectinload(ScheduleBlockModel.playlist_entries))
    )
    result = await db.execute(stmt)
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Schedule block not found")
    return block


@router.patch("/blocks/{block_id}", response_model=ScheduleBlock)
async def update_schedule_block(
    block_id: UUID,
    data: ScheduleBlockUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Update a schedule block."""
    stmt = select(ScheduleBlockModel).where(ScheduleBlockModel.id == block_id)
    result = await db.execute(stmt)
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Schedule block not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(block, key, value)

    await db.commit()
    await db.refresh(block)
    return block


@router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule_block(
    block_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Delete a schedule block."""
    stmt = select(ScheduleBlockModel).where(ScheduleBlockModel.id == block_id)
    result = await db.execute(stmt)
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Schedule block not found")

    await db.delete(block)
    await db.commit()


# ==================== Playlist Entries (before /{schedule_id}) ====================
@router.post("/playlist-entries", response_model=PlaylistEntry, status_code=status.HTTP_201_CREATED)
async def create_playlist_entry(
    data: PlaylistEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Add an asset to a schedule block's playlist."""
    entry = PlaylistEntryModel(**data.model_dump())
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


@router.get("/playlist-entries", response_model=List[PlaylistEntry])
async def list_playlist_entries(
    block_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all playlist entries, optionally filtered by block."""
    stmt = select(PlaylistEntryModel).offset(skip).limit(limit).order_by(PlaylistEntryModel.position)
    if block_id:
        stmt = stmt.where(PlaylistEntryModel.block_id == block_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/playlist-entries/{entry_id}", response_model=PlaylistEntry)
async def get_playlist_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single playlist entry by ID."""
    stmt = select(PlaylistEntryModel).where(PlaylistEntryModel.id == entry_id)
    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Playlist entry not found")
    return entry


@router.patch("/playlist-entries/{entry_id}", response_model=PlaylistEntry)
async def update_playlist_entry(
    entry_id: UUID,
    data: PlaylistEntryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Update a playlist entry."""
    stmt = select(PlaylistEntryModel).where(PlaylistEntryModel.id == entry_id)
    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Playlist entry not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(entry, key, value)

    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/playlist-entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Delete a playlist entry."""
    stmt = select(PlaylistEntryModel).where(PlaylistEntryModel.id == entry_id)
    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Playlist entry not found")

    await db.delete(entry)
    await db.commit()


# ==================== Timeline Preview ====================
@router.get("/timeline-preview")
async def timeline_preview(
    station_id: UUID = Query(...),
    at_time: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Preview what block is active and blackout status at a given time."""
    from app.models.holiday_window import HolidayWindow
    from app.models.station import Station
    from app.services.scheduling import SchedulingService

    now = datetime.utcnow()
    check_time = now
    if at_time:
        try:
            check_time = datetime.fromisoformat(at_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid at_time format. Use ISO 8601.")

    # Validate station exists
    stmt = select(Station).where(Station.id == station_id)
    result = await db.execute(stmt)
    station = result.scalar_one_or_none()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # Get active block
    svc = SchedulingService(db)
    block = await svc.get_active_block_for_station(station_id, at_time=check_time)
    active_block = None
    if block:
        # Get the schedule name
        sched_stmt = select(ScheduleModel).where(ScheduleModel.id == block.schedule_id)
        sched_result = await db.execute(sched_stmt)
        sched = sched_result.scalar_one_or_none()
        active_block = {
            "id": str(block.id),
            "name": block.name,
            "schedule_name": sched.name if sched else None,
            "start_time": block.start_time,
            "end_time": block.end_time,
            "playback_mode": block.playback_mode,
        }

    # Check blackout status (same pattern as scheduler_engine._is_station_blacked_out)
    is_blacked_out = False
    current_blackout = None
    blackout_stmt = select(HolidayWindow).where(
        HolidayWindow.is_blackout == True,
        HolidayWindow.start_datetime <= check_time,
        HolidayWindow.end_datetime > check_time,
    )
    blackout_result = await db.execute(blackout_stmt)
    for window in blackout_result.scalars().all():
        if window.affected_stations is None:
            is_blacked_out = True
            current_blackout = {
                "name": window.name,
                "start_datetime": window.start_datetime.isoformat(),
                "end_datetime": window.end_datetime.isoformat(),
            }
            break
        station_ids = window.affected_stations.get("station_ids", [])
        if str(station_id) in [str(sid) for sid in station_ids]:
            is_blacked_out = True
            current_blackout = {
                "name": window.name,
                "start_datetime": window.start_datetime.isoformat(),
                "end_datetime": window.end_datetime.isoformat(),
            }
            break

    # Find next upcoming blackout
    next_blackout = None
    next_stmt = (
        select(HolidayWindow)
        .where(
            HolidayWindow.is_blackout == True,
            HolidayWindow.start_datetime > check_time,
        )
        .order_by(HolidayWindow.start_datetime)
        .limit(10)
    )
    next_result = await db.execute(next_stmt)
    for window in next_result.scalars().all():
        if window.affected_stations is None or str(station_id) in [
            str(sid) for sid in (window.affected_stations or {}).get("station_ids", [])
        ]:
            next_blackout = {
                "name": window.name,
                "start_datetime": window.start_datetime.isoformat(),
                "end_datetime": window.end_datetime.isoformat(),
            }
            break

    return {
        "station_id": str(station_id),
        "at_time": check_time.isoformat(),
        "is_blacked_out": is_blacked_out,
        "active_block": active_block,
        "current_blackout": current_blackout,
        "next_blackout": next_blackout,
    }


# ==================== Schedule by ID (after literal routes) ====================
@router.get("/{schedule_id}", response_model=Schedule)
async def get_schedule(
    schedule_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single schedule by ID."""
    stmt = (
        select(ScheduleModel)
        .where(ScheduleModel.id == schedule_id)
        .options(selectinload(ScheduleModel.blocks).selectinload(ScheduleBlockModel.playlist_entries))
    )
    result = await db.execute(stmt)
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.patch("/{schedule_id}", response_model=Schedule)
async def update_schedule(
    schedule_id: UUID,
    data: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Update a schedule."""
    stmt = select(ScheduleModel).where(ScheduleModel.id == schedule_id)
    result = await db.execute(stmt)
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(schedule, key, value)

    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Delete a schedule."""
    stmt = select(ScheduleModel).where(ScheduleModel.id == schedule_id)
    result = await db.execute(stmt)
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    await db.delete(schedule)
    await db.commit()
