import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.responses import JSONResponse

from app.config import settings
from app.core.dependencies import get_current_user, require_manager
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.user import User
from app.schemas.queue import QueueAdd, QueueBulkAdd, QueueEntryOut, QueueListResponse, QueueReorder

logger = logging.getLogger(__name__)

DEFAULT_DURATION = 180  # 3 minutes fallback

router = APIRouter(prefix="/stations/{station_id}/queue", tags=["queue"])


async def _maybe_insert_hourly_jingle(db: AsyncSession, station_id: uuid.UUID) -> None:
    """Insert hourly station ID jingle at the top of the hour (within first 30s)."""
    now = datetime.now(timezone.utc)
    if now.minute != 0 or now.second > 30:
        return

    current_hour = now.hour
    # Check if we already played an hourly jingle recently (last 55 min)
    cutoff = now.replace(minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(PlayLog).join(Asset, PlayLog.asset_id == Asset.id)
        .where(
            PlayLog.station_id == station_id,
            Asset.category == "hourly_id",
            PlayLog.start_utc >= cutoff,
        )
        .limit(1)
    )
    if result.scalar_one_or_none():
        return  # Already played this hour

    # Also check if one is already queued
    result = await db.execute(
        select(QueueEntry).join(Asset, QueueEntry.asset_id == Asset.id)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            Asset.category == "hourly_id",
        )
        .limit(1)
    )
    if result.scalar_one_or_none():
        return  # Already queued

    # Find the jingle for this hour
    hour_label = f"{'12' if current_hour % 12 == 0 else current_hour % 12}:00 {'AM' if current_hour < 12 else 'PM'}"
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "jingle",
            Asset.category == "hourly_id",
            Asset.title.contains(hour_label),
        ).limit(1)
    )
    jingle = result.scalar_one_or_none()
    if not jingle:
        # Fallback: any hourly jingle
        result = await db.execute(
            select(Asset).where(Asset.asset_type == "jingle", Asset.category == "hourly_id").limit(1)
        )
        jingle = result.scalar_one_or_none()
    if not jingle:
        return

    # Insert as play-next: bump all pending positions and insert at position 1
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .values(position=QueueEntry.position + 1)
    )
    # Find current playing position
    result = await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
    )
    current = result.scalar_one_or_none()
    next_pos = (current.position + 1) if current else 1

    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=jingle.id,
        position=next_pos, status="pending",
    )
    db.add(entry)
    await db.flush()


async def _maybe_insert_weather_spot(db: AsyncSession, station_id: uuid.UUID) -> None:
    """Insert time announcement + weather spot at every 15-min boundary."""
    if not settings.elevenlabs_enabled or not settings.supabase_storage_enabled:
        return

    now = datetime.now(timezone.utc)
    # Check if we're within 30s of a 15-min boundary
    if now.minute % 15 != 0 or now.second > 30:
        return

    # Build slot key in Eastern time
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    eastern_now = now.astimezone(ZoneInfo("America/New_York"))
    slot_key = eastern_now.strftime("%Y-%m-%dT%H:%M")

    # Dedup: check PlayLog for weather already played this slot
    slot_start = now.replace(second=0, microsecond=0)
    result = await db.execute(
        select(PlayLog).join(Asset, PlayLog.asset_id == Asset.id)
        .where(
            PlayLog.station_id == station_id,
            Asset.category.in_(["time_announcement", "weather_spot"]),
            PlayLog.start_utc >= slot_start,
        )
        .limit(1)
    )
    if result.scalar_one_or_none():
        return

    # Dedup: check QueueEntry for weather already queued this slot
    result = await db.execute(
        select(QueueEntry).join(Asset, QueueEntry.asset_id == Asset.id)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            Asset.category.in_(["time_announcement", "weather_spot"]),
        )
        .limit(1)
    )
    if result.scalar_one_or_none():
        return

    # Generate assets (TTS + weather fetch + upload)
    try:
        from app.services.weather_spot_service import get_or_create_weather_spot_assets

        time_asset, weather_asset = await get_or_create_weather_spot_assets(db, slot_key)
    except Exception:
        logger.warning("Failed to create weather spot assets for slot %s", slot_key, exc_info=True)
        return

    if not time_asset and not weather_asset:
        return

    # Insert as play-next (time first, weather second)
    # Bump all pending positions
    assets_to_insert = [a for a in [time_asset, weather_asset] if a is not None]
    if not assets_to_insert:
        return

    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .values(position=QueueEntry.position + len(assets_to_insert))
    )

    result = await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
    )
    current = result.scalar_one_or_none()
    next_pos = (current.position + 1) if current else 1

    for i, asset in enumerate(assets_to_insert):
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=station_id,
            asset_id=asset.id,
            position=next_pos + i,
            status="pending",
        )
        db.add(entry)

    await db.flush()
    logger.info("Inserted weather spot for slot %s (%d items)", slot_key, len(assets_to_insert))


async def _replenish_queue(db: AsyncSession, station_id: uuid.UUID) -> None:
    """Auto-fill queue to ~24 hours of content using schedule rules."""
    from app.services.queue_replenish_service import QueueReplenishService
    
    service = QueueReplenishService(db, station_id)
    await service.replenish()


async def _check_advance(db: AsyncSession, station_id: uuid.UUID) -> QueueEntry | None:
    """Core playback engine: check if current track is done and auto-advance."""
    # Check for hourly jingle insertion
    await _maybe_insert_hourly_jingle(db, station_id)
    # Check for weather/time spot insertion every 15 min
    await _maybe_insert_weather_spot(db, station_id)

    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    playing_entries = result.scalars().all()
    current = playing_entries[0] if playing_entries else None

    # Clean up duplicate "playing" entries — keep only the most recent
    if len(playing_entries) > 1:
        for extra in playing_entries[1:]:
            extra.status = "played"
        await db.flush()
        logger.warning("_check_advance: cleaned up %d duplicate playing entries", len(playing_entries) - 1)

    if not current:
        return None

    # If no started_at, set it now
    if not current.started_at:
        current.started_at = datetime.now(timezone.utc)
        await db.commit()
        return current

    # Check if track duration has elapsed
    asset = current.asset
    duration = asset.duration if asset else 0
    if not duration:
        duration = 180  # default 3 min

    elapsed = (datetime.now(timezone.utc) - current.started_at).total_seconds()
    if elapsed < duration:
        return current  # still playing

    # Track finished — log it and advance
    log = PlayLog(
        id=uuid.uuid4(),
        station_id=station_id,
        asset_id=current.asset_id,
        start_utc=current.started_at,
        end_utc=datetime.now(timezone.utc),
        source="scheduler",
    )
    db.add(log)
    current.status = "played"

    # Find next pending
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .limit(1)
    )
    next_entry = result.scalar_one_or_none()
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)
        await _replenish_queue(db, station_id)
        await db.commit()
        return next_entry

    await db.commit()
    return None


@router.get("")
async def get_queue(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Run the playback engine on every poll
    try:
        now_playing_entry = await _check_advance(db, station_id)
    except Exception as exc:
        logger.error("_check_advance failed: %s", exc, exc_info=True)
        now_playing_entry = None

    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )
    entries = result.scalars().all()

    # Calculate elapsed/remaining for now playing
    np_data = None
    if now_playing_entry and now_playing_entry.started_at:
        asset = now_playing_entry.asset
        duration = asset.duration if asset else 180
        elapsed = (datetime.now(timezone.utc) - now_playing_entry.started_at).total_seconds()
        remaining = max(0, duration - elapsed)
        np_data = {
            "id": str(now_playing_entry.id),
            "station_id": str(now_playing_entry.station_id),
            "asset_id": str(now_playing_entry.asset_id),
            "position": now_playing_entry.position,
            "status": now_playing_entry.status,
            "asset": now_playing_entry.asset,
            "started_at": now_playing_entry.started_at.isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "remaining_seconds": round(remaining, 1),
        }

    entries_data = []
    queue_duration = 0.0
    for e in entries:
        dur = (e.asset.duration if e.asset and e.asset.duration else DEFAULT_DURATION)
        queue_duration += dur
        d = {
            "id": str(e.id),
            "station_id": str(e.station_id),
            "asset_id": str(e.asset_id),
            "position": e.position,
            "status": e.status,
            "asset": {
                "id": str(e.asset.id),
                "title": e.asset.title,
                "artist": e.asset.artist,
                "album": e.asset.album,
                "duration": e.asset.duration,
                "file_path": e.asset.file_path,
                "album_art_path": e.asset.album_art_path,
                "metadata_extra": e.asset.metadata_extra,
                "created_by": str(e.asset.created_by) if e.asset.created_by else None,
                "asset_type": e.asset.asset_type,
                "category": e.asset.category,
            } if e.asset else None,
        }
        entries_data.append(d)

    return {
        "entries": entries_data,
        "total": len(entries_data),
        "now_playing": np_data,
        "queue_duration_seconds": round(queue_duration, 1),
    }


@router.get("/log")
async def get_play_log(
    station_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get recent play history."""
    result = await db.execute(
        select(PlayLog)
        .where(PlayLog.station_id == station_id)
        .order_by(PlayLog.start_utc.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return {
        "logs": [
            {
                "id": str(log.id),
                "asset_id": str(log.asset_id) if log.asset_id else None,
                "title": log.asset.title if log.asset else "Unknown",
                "artist": log.asset.artist if log.asset else None,
                "asset_type": log.asset.asset_type if log.asset else None,
                "start_utc": log.start_utc.isoformat(),
                "end_utc": log.end_utc.isoformat() if log.end_utc else None,
                "source": log.source.value if hasattr(log.source, 'value') else str(log.source),
            }
            for log in logs
        ],
        "total": len(logs),
    }


@router.get("/last-played")
async def get_last_played(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get the most recent play time for each asset."""
    result = await db.execute(
        select(PlayLog.asset_id, func.max(PlayLog.start_utc))
        .where(PlayLog.station_id == station_id, PlayLog.asset_id.isnot(None))
        .group_by(PlayLog.asset_id)
    )
    rows = result.all()
    return {
        "last_played": {
            str(asset_id): ts.isoformat() if ts else None
            for asset_id, ts in rows
        }
    }


@router.post("/add", status_code=201)
async def add_to_queue(
    station_id: uuid.UUID,
    body: QueueAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )
    max_pos = result.scalar() or 0
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=body.asset_id,
        position=max_pos + 1, status="pending",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"id": str(entry.id), "position": entry.position, "status": entry.status}


@router.post("/bulk-add", status_code=201)
async def bulk_add_to_queue(
    station_id: uuid.UUID,
    body: QueueBulkAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )
    max_pos = result.scalar() or 0
    count = 0
    for asset_id in body.asset_ids:
        max_pos += 1
        entry = QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset_id,
            position=max_pos, status="pending",
        )
        db.add(entry)
        count += 1
    await db.commit()
    return {"message": f"Added {count} items to queue", "count": count}


@router.post("/play-next", status_code=201)
async def play_next(
    station_id: uuid.UUID,
    body: QueueAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .values(position=QueueEntry.position + 1)
    )
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
    )
    current = result.scalar_one_or_none()
    next_pos = (current.position + 1) if current else 1
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=body.asset_id,
        position=next_pos, status="pending",
    )
    db.add(entry)
    await db.commit()
    return {"id": str(entry.id), "position": entry.position, "message": "Queued as next"}


@router.post("/skip")
async def skip_current(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    playing_entries = result.scalars().all()
    current = playing_entries[0] if playing_entries else None
    # Clean up extras
    for extra in playing_entries[1:]:
        extra.status = "skipped"
    if current:
        # Log the skip
        if current.started_at:
            log = PlayLog(
                id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                start_utc=current.started_at, end_utc=datetime.now(timezone.utc),
                source="manual",
            )
            db.add(log)
        current.status = "skipped"

    # Advance to next
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).limit(1)
    )
    next_entry = result.scalar_one_or_none()
    await _replenish_queue(db, station_id)
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)
        await db.commit()
        return {"message": "Skipped", "now_playing": str(next_entry.asset_id)}

    await db.commit()
    return {"message": "Queue empty", "now_playing": None}


@router.post("/move-up")
async def move_up(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Move a queue entry up (lower position number)."""
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry or entry.status != "pending":
        raise NotFoundError("Entry not found or not pending")

    # Find the entry above it
    result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position < entry.position,
        )
        .order_by(QueueEntry.position.desc()).limit(1)
    )
    above = result.scalar_one_or_none()
    if above:
        above.position, entry.position = entry.position, above.position
        await db.commit()
    return {"message": "Moved up"}


@router.post("/move-down")
async def move_down(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Move a queue entry down (higher position number)."""
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry or entry.status != "pending":
        raise NotFoundError("Entry not found or not pending")

    result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position > entry.position,
        )
        .order_by(QueueEntry.position.asc()).limit(1)
    )
    below = result.scalar_one_or_none()
    if below:
        below.position, entry.position = entry.position, below.position
        await db.commit()
    return {"message": "Moved down"}


@router.put("/reorder")
async def reorder_queue(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    entry.position = body.new_position
    await db.commit()
    return {"message": "Reordered"}


@router.delete("/{entry_id}", status_code=204)
async def remove_from_queue(
    station_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    await db.delete(entry)
    await _replenish_queue(db, station_id)
    await db.commit()


@router.post("/start")
async def start_playback(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    import traceback
    try:
        result = await db.execute(
            select(QueueEntry)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc().nullslast())
        )
        playing_entries = result.scalars().all()
        current = playing_entries[0] if playing_entries else None

        # Clean up duplicate "playing" entries — keep only the most recent
        if len(playing_entries) > 1:
            for extra in playing_entries[1:]:
                extra.status = "played"
            await db.flush()
            logger.warning("Cleaned up %d duplicate playing entries", len(playing_entries) - 1)

        if current:
            if not current.started_at:
                current.started_at = datetime.now(timezone.utc)
                await db.commit()
            return {"message": "Already playing", "now_playing": str(current.asset_id)}

        # Auto-fill queue before starting
        await _replenish_queue(db, station_id)

        result = await db.execute(
            select(QueueEntry)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
            .order_by(QueueEntry.position).limit(1)
        )
        next_entry = result.scalar_one_or_none()
        if not next_entry:
            await db.commit()
            return {"message": "Queue empty — no music assets available", "now_playing": None}

        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)
        await db.commit()
        return {"message": "Started", "now_playing": str(next_entry.asset_id)}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("start_playback error: %s\n%s", exc, tb)
        return JSONResponse({"error": str(exc), "traceback": tb}, status_code=500)


@router.get("/debug")
async def debug_queue(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Debug endpoint to diagnose queue issues."""
    import traceback
    steps = []
    try:
        # Step 1: Count queue entries
        result = await db.execute(
            select(func.count(QueueEntry.id))
            .where(QueueEntry.station_id == station_id)
        )
        total = result.scalar() or 0
        steps.append(f"1. Total queue entries: {total}")

        # Step 2: Count by status
        for status in ["pending", "playing", "played", "skipped"]:
            result = await db.execute(
                select(func.count(QueueEntry.id))
                .where(QueueEntry.station_id == station_id, QueueEntry.status == status)
            )
            count = result.scalar() or 0
            steps.append(f"2. Status '{status}': {count}")

        # Step 3: Count assets
        result = await db.execute(select(func.count(Asset.id)))
        asset_count = result.scalar() or 0
        steps.append(f"3. Total assets: {asset_count}")

        # Step 4: Count music assets
        result = await db.execute(
            select(func.count(Asset.id)).where(Asset.asset_type == "music")
        )
        music_count = result.scalar() or 0
        steps.append(f"4. Music assets: {music_count}")

        # Step 5: Test replenish
        try:
            from app.services.queue_replenish_service import QueueReplenishService
            service = QueueReplenishService(db, station_id)
            await service.replenish()
            steps.append("5. Replenish: SUCCESS")
        except Exception as e:
            steps.append(f"5. Replenish FAILED: {e}")
            steps.append(traceback.format_exc())

        # Step 6: Count queue again
        result = await db.execute(
            select(func.count(QueueEntry.id))
            .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        )
        active_count = result.scalar() or 0
        steps.append(f"6. Active queue entries after replenish: {active_count}")

        await db.commit()
        return {"steps": steps}
    except Exception as e:
        return JSONResponse({"error": str(e), "traceback": traceback.format_exc(), "steps": steps}, status_code=500)


@router.post("/preview-weather")
async def preview_weather(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Generate (or return cached) weather + time TTS for preview — does NOT insert into queue."""
    if not settings.elevenlabs_enabled or not settings.supabase_storage_enabled:
        return JSONResponse({"error": "ElevenLabs or Supabase storage not configured"}, status_code=503)

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    now = datetime.now(timezone.utc)
    eastern_now = now.astimezone(ZoneInfo("America/New_York"))
    # Round down to nearest 15-min slot
    rounded_minute = (eastern_now.minute // 15) * 15
    slot_key = eastern_now.replace(minute=rounded_minute, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")

    from app.services.weather_spot_service import get_or_create_weather_spot_assets, _build_time_text, _build_weather_text
    from app.services.weather_service import get_current_weather

    time_asset, weather_asset = await get_or_create_weather_spot_assets(db, slot_key)
    await db.commit()

    # Build text for display
    time_text = _build_time_text(eastern_now)
    try:
        weather_data = await get_current_weather()
        weather_text = _build_weather_text(weather_data)
    except Exception:
        weather_text = None

    return {
        "time_url": time_asset.file_path if time_asset else None,
        "weather_url": weather_asset.file_path if weather_asset else None,
        "time_text": time_text,
        "weather_text": weather_text,
    }
