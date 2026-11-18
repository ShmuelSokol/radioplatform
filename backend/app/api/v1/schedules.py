"""
Schedule management endpoints — CRUD for schedules, blocks, and playlist entries.
"""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import station_guard as sg
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

router = APIRouter(
    prefix="/schedules",
    tags=["schedules"],
    dependencies=[
        Depends(
            sg.station_write_guard(
                path={"schedule_id": sg.of_schedule, "block_id": sg.of_block, "entry_id": sg.of_playlist_entry},
                body={
                    "schedule_id": sg.of_schedule,
                    "block_id": sg.of_block,
                    "winner_block_id": sg.of_block,
                    "other_block_ids": sg.of_block,
                },
            )
        )
    ],
)


# ==================== Public EPG ====================
def _hhmm(local: datetime) -> str:
    return local.strftime("%H:%M")


@router.get("/epg/{station_id}")
async def get_epg(
    station_id: UUID,
    date: str | None = Query(None),  # YYYY-MM-DD, defaults to the station's today
    db: AsyncSession = Depends(get_db),
):
    """Public EPG: what airs on a station on one day of its own calendar, in order.

    Built with the resolver the station plays by (SchedulingService.rank_matching_blocks),
    so the guide agrees with what airs (V2-105): a sunset/sunrise block shows that
    day's real time, overlapping blocks show only the winner for the time it wins,
    a start-only ("until next") block ends where the next one starts, and Shabbos /
    Yom Tov blackouts show as off air. Times are station-local HH:MM; a run that goes
    on past midnight ends "00:00".
    """
    from datetime import date as date_type
    from datetime import timedelta

    from app.models.station import Station
    from app.services.scheduling import SchedulingService
    from app.services.spot_schedule_service import load_blackouts, station_tz

    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    # The station's own clock (block times are station-local), not the server's
    zone = station_tz(station.timezone if station else None)
    if date:
        try:
            target_date = date_type.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    else:
        target_date = datetime.now(zone).date()

    schedules = (
        await db.execute(
            select(ScheduleModel)
            .where(ScheduleModel.station_id == station_id, ScheduleModel.is_active == True)  # noqa: E712
            .options(selectinload(ScheduleModel.blocks))
        )
    ).scalars().all()
    if not schedules:
        return {"station_id": station_id, "date": target_date.isoformat(), "blocks": []}

    svc = SchedulingService(db)
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=zone)
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    start_utc, end_utc = day_start.astimezone(timezone.utc), day_end.astimezone(timezone.utc)
    blackouts = await load_blackouts(db, station_id, start_utc, end_utc)

    # Every moment the winner can change: midnight, each block's real start and end
    # that day (sun times resolved), and the edges of off-air windows.
    edges = {start_utc}
    for schedule in schedules:
        for block in schedule.blocks:
            for clock, event, offset in (
                (block.start_time, block.start_sun_event, block.start_sun_offset),
                (block.end_time, block.end_sun_event, block.end_sun_offset),
            ):
                if clock is None:
                    continue
                if event and station:
                    clock = svc._resolve_sun_time(event, offset or 0, station, target_date, fallback=clock)
                at = datetime.combine(target_date, clock, tzinfo=zone).astimezone(timezone.utc)
                if start_utc < at < end_utc:
                    edges.add(at)
    for b in blackouts:
        for at in (b.start, b.end):
            if start_utc < at < end_utc:
                edges.add(at)
    edges = sorted(edges)

    names = {s.id: s.name for s in schedules}
    runs: list[dict] = []
    for i, at in enumerate(edges):
        until = edges[i + 1] if i + 1 < len(edges) else end_utc
        off = next((b for b in blackouts if b.covers(at)), None)
        if off is not None:
            key, item = f"off:{off.name}", {
                "id": f"off-air-{at.isoformat()}",
                "name": off.name or "Off air",
                "description": "Off the air",
                "playback_mode": "off_air",
                "schedule_name": None,
                "off_air": True,
            }
        else:
            ranked = svc.rank_matching_blocks(schedules, at, station)
            if not ranked:
                continue
            schedule, block = ranked[0]
            key, item = str(block.id), {
                "id": str(block.id),
                "name": block.name,
                "description": block.description,
                "playback_mode": block.playback_mode.value if hasattr(block.playback_mode, "value") else str(block.playback_mode),
                "schedule_name": names.get(schedule.id),
                "off_air": False,
            }
        if runs and runs[-1]["_key"] == key and runs[-1]["_until"] == at:
            runs[-1]["_until"] = until
            continue
        runs.append({**item, "_key": key, "_from": at, "_until": until})

    epg_blocks = []
    for run in runs:
        item = {k: v for k, v in run.items() if not k.startswith("_")}
        item["start_time"] = _hhmm(run["_from"].astimezone(zone))
        item["end_time"] = _hhmm(run["_until"].astimezone(zone))
        epg_blocks.append(item)

    return {
        "station_id": station_id,
        "date": target_date.isoformat(),
        "blocks": epg_blocks,
    }


async def _queue_follows_schedule(db: AsyncSession, schedule_id, *, had_playlists: bool = False) -> None:
    """After a schedule or block change, regenerate the songs not yet played (see
    schedule_refresh_service) so the change is on the air from its start time."""
    from app.services.schedule_refresh_service import refresh_station, station_of_schedule

    try:
        station_id = await station_of_schedule(db, schedule_id)
        if station_id is not None:
            from app.api.v1 import playlists as _pl

            if _pl.REFRESH_INLINE:
                await refresh_station(db, station_id, had_playlists=had_playlists)
            else:
                # answer the save at once; the queue rebuild follows in the background
                _pl._schedule_refresh(station_id, had_playlists)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Queue refresh after schedule change failed")
        await db.rollback()


async def _block_has_songs(db: AsyncSession, block_id) -> bool:
    row = await db.execute(
        select(PlaylistEntryModel.id)
        .where(PlaylistEntryModel.block_id == block_id, PlaylistEntryModel.is_enabled.is_(True))
        .limit(1)
    )
    return row.first() is not None


async def _queue_follows_block(db: AsyncSession, block_id, *, had_playlists: bool = False) -> None:
    """A song added to, changed on or removed from a block reaches the queue on time."""
    schedule_id = (
        await db.execute(select(ScheduleBlockModel.schedule_id).where(ScheduleBlockModel.id == block_id))
    ).scalar_one_or_none()
    if schedule_id is not None:
        await _queue_follows_schedule(db, schedule_id, had_playlists=had_playlists)


# ==================== Schedules ====================
# Also answered without the trailing slash: the site calls /schedules, and the
# automatic redirect to /schedules/ points at http:// behind the proxy, which the
# browser blocks, so "Create" silently did nothing.
@router.post("", response_model=Schedule, status_code=status.HTTP_201_CREATED, include_in_schema=False)
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


@router.get("", response_model=List[Schedule], include_in_schema=False)
@router.get("/", response_model=List[Schedule])
async def list_schedules(
    station_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all schedules, optionally filtered by station. The same list for every
    role, in a fixed order (highest priority first, then by name)."""
    stmt = select(ScheduleModel).options(selectinload(ScheduleModel.blocks))
    if station_id:
        stmt = stmt.where(ScheduleModel.station_id == station_id)
    stmt = stmt.order_by(ScheduleModel.priority.desc(), ScheduleModel.name, ScheduleModel.id).offset(skip).limit(limit)
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
    values = data.model_dump()
    if isinstance(values.get("playlist_template_id"), str):
        values["playlist_template_id"] = UUID(values["playlist_template_id"])
    block = ScheduleBlockModel(**values)
    db.add(block)
    await db.commit()
    await db.refresh(block)

    # Check for scheduling conflicts
    try:
        from app.services.alert_service import detect_schedule_conflicts
        await detect_schedule_conflicts(db, block.schedule_id, block.id)
        await db.commit()
    except Exception:
        pass  # Don't fail block creation if conflict detection errors

    await _queue_follows_schedule(db, block.schedule_id)
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

    had_playlist = block.playlist_template_id is not None or await _block_has_songs(db, block.id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if key == "playlist_template_id" and isinstance(value, str):
            value = UUID(value)
        setattr(block, key, value)

    await db.commit()
    await db.refresh(block)

    # Check for scheduling conflicts after update
    try:
        from app.services.alert_service import detect_schedule_conflicts
        await detect_schedule_conflicts(db, block.schedule_id, block.id)
        await db.commit()
    except Exception:
        pass

    await _queue_follows_schedule(db, block.schedule_id, had_playlists=had_playlist)
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

    schedule_id = block.schedule_id
    had_playlist = block.playlist_template_id is not None or await _block_has_songs(db, block.id)
    await db.delete(block)
    await db.commit()
    await _queue_follows_schedule(db, schedule_id, had_playlists=had_playlist)


@router.post("/blocks/{block_id}/set-prerecorded", status_code=200)
async def set_prerecorded_show(
    block_id: UUID,
    body: dict,  # {"asset_id": "uuid-string"}
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Set a block to play a single pre-recorded show asset."""
    from app.core.exceptions import NotFoundError

    result = await db.execute(
        select(ScheduleBlockModel).where(ScheduleBlockModel.id == block_id)
    )
    block = result.scalar_one_or_none()
    if not block:
        raise NotFoundError("Block not found")

    had_songs = await _block_has_songs(db, block_id)
    # Clear existing entries
    existing = (await db.execute(
        select(PlaylistEntryModel).where(PlaylistEntryModel.block_id == block_id)
    )).scalars().all()
    for entry in existing:
        await db.delete(entry)

    # Add single asset
    entry = PlaylistEntryModel(
        block_id=block_id,
        asset_id=body["asset_id"],
        position=0,
        is_enabled=True,
    )
    db.add(entry)
    await db.commit()
    await _queue_follows_block(db, block_id, had_playlists=had_songs)

    return {"status": "ok", "block_id": str(block_id), "asset_id": body["asset_id"]}


# ==================== Playlist Entries (before /{schedule_id}) ====================
@router.post("/playlist-entries", response_model=PlaylistEntry, status_code=status.HTTP_201_CREATED)
async def create_playlist_entry(
    data: PlaylistEntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Add an asset to a schedule block's playlist."""
    entry = PlaylistEntryModel(**data.model_dump())
    block_id = entry.block_id
    db.add(entry)
    await db.commit()
    await _queue_follows_block(db, block_id)
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

    block_id = entry.block_id
    had_songs = await _block_has_songs(db, block_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(entry, key, value)

    await db.commit()
    await _queue_follows_block(db, block_id, had_playlists=had_songs)
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

    block_id = entry.block_id
    had_songs = await _block_has_songs(db, block_id)
    await db.delete(entry)
    await db.commit()
    await _queue_follows_block(db, block_id, had_playlists=had_songs)


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

    now = datetime.now(timezone.utc)
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
# ==================== Look-ahead (before /{schedule_id}) ====================


@router.get("/look-ahead")
async def schedule_look_ahead(
    station_id: UUID = Query(...),
    days: int = Query(7, ge=1, le=14),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    """What collides over the coming days, how each collision is settled,
    and what still needs a person. See wiki/schedule-look-ahead.md."""
    from app.models.station import Station
    from app.services.schedule_lookahead_service import look_ahead

    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return await look_ahead(db, station, days=days)


class SettleOverlap(BaseModel):
    winner_block_id: UUID
    other_block_ids: List[UUID]


@router.post("/look-ahead/settle")
async def settle_schedule_overlap(
    data: SettleOverlap,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    """Choose which of the overlapping blocks plays, by raising its priority above the others."""
    from app.services.schedule_lookahead_service import settle_overlap

    try:
        winner = await settle_overlap(db, data.winner_block_id, data.other_block_ids)
    except LookupError:
        raise HTTPException(status_code=404, detail="Schedule block not found")
    await db.commit()
    return {"block_id": str(winner.id), "priority": winner.priority}


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

    from app.services.schedule_refresh_service import uses_block_playlists

    had = await uses_block_playlists(db, schedule.station_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(schedule, key, value)

    await db.commit()
    await _queue_follows_schedule(db, schedule_id, had_playlists=had)
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

    from app.services.schedule_refresh_service import refresh_station, uses_block_playlists

    station_id = schedule.station_id
    had = await uses_block_playlists(db, station_id)
    await db.delete(schedule)
    await db.commit()
    try:
        from app.api.v1 import playlists as _pl

        if _pl.REFRESH_INLINE:
            await refresh_station(db, station_id, had_playlists=had)
        else:
            _pl._schedule_refresh(station_id, had)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Queue refresh after schedule delete failed")
        await db.rollback()
