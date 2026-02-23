import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.responses import JSONResponse

from app.config import settings
from app.core.dependencies import get_current_user, require_dj_or_manager, require_manager
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.user import User
from app.schemas.queue import QueueAdd, QueueBulkAdd, QueueDndReorder, QueueEntryOut, QueueListResponse, QueueReorder

from app.models.holiday_window import HolidayWindow

logger = logging.getLogger(__name__)

DEFAULT_DURATION = 180  # 3 minutes fallback


async def _is_blacked_out(db: AsyncSession, station_id) -> bool:
    """Check if a station is currently in a blackout window."""
    now = datetime.now(timezone.utc)
    stmt = select(HolidayWindow).where(
        HolidayWindow.is_blackout == True,
        HolidayWindow.start_datetime <= now,
        HolidayWindow.end_datetime > now,
    )
    result = await db.execute(stmt)
    windows = result.scalars().all()
    for window in windows:
        if window.affected_stations is None:
            return True
        station_ids = window.affected_stations.get("station_ids", [])
        if str(station_id) in [str(sid) for sid in station_ids]:
            return True
    return False


def _get_blackout_window_for_station(windows: list, station_id) -> "HolidayWindow | None":
    """Return the active/upcoming blackout window affecting this station."""
    for w in windows:
        if w.affected_stations is None:
            return w
        sids = w.affected_stations.get("station_ids", [])
        if str(station_id) in [str(s) for s in sids]:
            return w
    return None


async def fill_blackout_queue(db: AsyncSession, station_id, window: "HolidayWindow | None" = None):
    """Fill queue with silence entries covering blackout window(s).

    When called without a specific window, processes ALL upcoming blackouts
    for the station. Each blackout's silence entries are tracked separately
    via preempt_at (future) or absence of preempt_at (active).
    """
    if window:
        return await _fill_single_blackout(db, station_id, window)

    now = datetime.now(timezone.utc)

    # Clean up stale silence entries from far-future blackouts (> 24h away)
    silence_result = await db.execute(select(Asset).where(Asset.asset_type == "silence").limit(1))
    silence_asset = silence_result.scalar_one_or_none()
    if silence_asset:
        cleanup = await db.execute(
            select(func.count(QueueEntry.id)).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id == silence_asset.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > now + timedelta(hours=24),
            )
        )
        stale_count = cleanup.scalar() or 0
        if stale_count > 0:
            await db.execute(
                update(QueueEntry).where(
                    QueueEntry.station_id == station_id,
                    QueueEntry.asset_id == silence_asset.id,
                    QueueEntry.status == "pending",
                    QueueEntry.preempt_at.isnot(None),
                    QueueEntry.preempt_at > now + timedelta(hours=24),
                ).values(status="played")
            )
            await db.commit()
            logger.info("Cleaned up %d stale far-future silence entries for station %s", stale_count, station_id)

    # Find active + near-future blackouts (within 24h) for this station
    stmt = select(HolidayWindow).where(
        HolidayWindow.is_blackout == True,
        HolidayWindow.end_datetime > now,
        HolidayWindow.start_datetime <= now + timedelta(hours=24),
    ).order_by(HolidayWindow.start_datetime)
    result = await db.execute(stmt)
    all_windows = result.scalars().all()

    # Collect IDs of windows affecting this station (before any commits expire objects)
    window_ids = []
    for w in all_windows:
        if _get_blackout_window_for_station([w], station_id):
            window_ids.append(w.id)

    total = 0
    for wid in window_ids:
        # Re-fetch each window fresh (previous _fill_single_blackout commits expire objects)
        w_result = await db.execute(select(HolidayWindow).where(HolidayWindow.id == wid))
        w = w_result.scalar_one_or_none()
        if w:
            total += await _fill_single_blackout(db, station_id, w)

    # Compact positions after fill to prevent bloat from repeated bump operations
    if total > 0:
        compact_result = await db.execute(
            select(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
            .order_by(QueueEntry.position)
        )
        compact_entries = compact_result.scalars().all()
        for i, entry in enumerate(compact_entries):
            entry.position = i + 1
        await db.commit()
        logger.info("Compacted %d queue positions for station %s", len(compact_entries), station_id)

    return total


async def _fill_single_blackout(db: AsyncSession, station_id, window: "HolidayWindow"):
    """Fill queue with silence for a single blackout window."""
    import math

    now = datetime.now(timezone.utc)

    # Get silence asset
    silence_result = await db.execute(select(Asset).where(Asset.asset_type == "silence").limit(1))
    silence = silence_result.scalar_one_or_none()
    if not silence:
        logger.warning("No silence asset found for blackout queue fill")
        return 0

    silence_duration = silence.duration or 300.0
    blackout_start = window.start_datetime
    blackout_end = window.end_datetime

    # Make timezone-aware if needed
    if blackout_start.tzinfo is None:
        blackout_start = blackout_start.replace(tzinfo=timezone.utc)
    if blackout_end.tzinfo is None:
        blackout_end = blackout_end.replace(tzinfo=timezone.utc)

    # Determine if blackout is active now or in the future
    is_active_now = blackout_start <= now
    total_seconds = (blackout_end - blackout_start).total_seconds()
    if is_active_now:
        total_seconds = (blackout_end - now).total_seconds()
    if total_seconds <= 0:
        return 0

    num_silence = math.ceil(total_seconds / silence_duration)

    # Count existing silence entries for THIS specific blackout
    if is_active_now:
        # Active blackout: count silence without preempt_at or with past preempt_at
        existing_silence = await db.execute(
            select(func.count(QueueEntry.id)).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id == silence.id,
                QueueEntry.status.in_(["pending", "playing"]),
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now),
            )
        )
    else:
        # Future blackout: count silence with preempt_at matching this blackout's start
        existing_silence = await db.execute(
            select(func.count(QueueEntry.id)).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id == silence.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at >= blackout_start - timedelta(minutes=2),
                QueueEntry.preempt_at <= blackout_start + timedelta(minutes=2),
            )
        )
    existing_count = existing_silence.scalar() or 0
    if existing_count >= num_silence:
        return 0  # Already filled

    if is_active_now:
        # Stop current music immediately
        playing_result = await db.execute(
            select(QueueEntry).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "playing",
            )
        )
        for entry in playing_result.scalars().all():
            if entry.asset_id != silence.id:
                entry.status = "played"

        # Clear THIS blackout's silence entries (active: no preempt_at or past)
        await db.execute(
            update(QueueEntry).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id == silence.id,
                QueueEntry.status.in_(["pending", "playing"]),
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now),
            ).values(status="played")
        )
    else:
        # Clear THIS future blackout's silence only (matching preempt_at)
        await db.execute(
            update(QueueEntry).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id == silence.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at >= blackout_start - timedelta(minutes=2),
                QueueEntry.preempt_at <= blackout_start + timedelta(minutes=2),
            ).values(status="played")
        )

    if is_active_now:
        # Active blackout: insert silence at the front (position 1..N)
        await db.execute(
            update(QueueEntry).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
            ).values(position=QueueEntry.position + num_silence + 1)
        )
        insert_at = 0
    else:
        # Future blackout: calculate the correct queue position where the
        # blackout starts by walking through playable entries + summing durations
        from sqlalchemy.orm import joinedload as jl

        cursor = now
        # Account for currently playing entry's remaining time
        playing_q = await db.execute(
            select(QueueEntry).options(jl(QueueEntry.asset))
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        )
        playing_entry = playing_q.unique().scalar_one_or_none()
        if playing_entry and playing_entry.started_at and playing_entry.asset:
            dur = playing_entry.asset.duration or DEFAULT_DURATION
            el = (now - playing_entry.started_at).total_seconds()
            cursor += timedelta(seconds=max(0, dur - el))

        # Walk through playable pending entries (skip future-preempt silence)
        pending_q = await db.execute(
            select(QueueEntry).options(jl(QueueEntry.asset))
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now),
            )
            .order_by(QueueEntry.position)
        )
        playable = pending_q.unique().scalars().all()

        insert_at = 0
        for pe in playable:
            dur = pe.asset.duration if pe.asset and pe.asset.duration else DEFAULT_DURATION
            if cursor >= blackout_start:
                break
            cursor += timedelta(seconds=dur)
            insert_at = pe.position

        # Bump only entries after the insertion point
        await db.execute(
            update(QueueEntry).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.position > insert_at,
            ).values(position=QueueEntry.position + num_silence)
        )

    for i in range(num_silence):
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=station_id,
            asset_id=silence.id,
            position=insert_at + i + 1,
            status="playing" if (i == 0 and is_active_now) else "pending",
            started_at=now if (i == 0 and is_active_now) else None,
            preempt_at=blackout_start if not is_active_now else None,
            source="auto",
        )
        db.add(entry)

    await db.commit()
    logger.info(
        "Filled blackout queue for station %s: %d silence entries (%.0f min), %s, ends at %s",
        station_id, num_silence, total_seconds / 60,
        "active" if is_active_now else f"starts {blackout_start.isoformat()}",
        blackout_end.isoformat(),
    )
    return num_silence
_last_advance: dict[str, float] = {}
ADVANCE_THROTTLE = 1.0  # seconds between _check_advance calls per station

router = APIRouter(prefix="/stations/{station_id}/queue", tags=["queue"])


async def _maybe_insert_hourly_jingle(db: AsyncSession, station_id: uuid.UUID) -> None:
    """Insert hourly station ID jingle at the top of the hour (within first 5 min)."""
    now = datetime.now(timezone.utc)
    if now.minute > 5:
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
    """Insert time announcement + weather spot at every hour (top of the hour)."""
    if not settings.elevenlabs_enabled or not settings.supabase_storage_enabled:
        logger.debug("Weather spot skipped: elevenlabs=%s supabase=%s", settings.elevenlabs_enabled, settings.supabase_storage_enabled)
        return

    now = datetime.now(timezone.utc)
    # Check if we're within 5 minutes of the top of the hour (widened from 30s)
    if now.minute > 5:
        return

    # Build slot key in Eastern time (hourly for time, daily for weather)
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    eastern_now = now.astimezone(ZoneInfo("America/New_York"))
    slot_key = eastern_now.strftime("%Y-%m-%dT%H")

    logger.info("Weather spot check for station %s at %s (slot %s)", station_id, now.isoformat(), slot_key)

    # Dedup: check PlayLog for weather already played this slot
    slot_start = now.replace(minute=0, second=0, microsecond=0)
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
        logger.info("Weather spot already played this hour for station %s", station_id)
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
        logger.info("Weather spot already queued for station %s", station_id)
        return

    # Generate assets (TTS + weather fetch + upload)
    logger.info("Generating weather/time TTS for station %s slot %s", station_id, slot_key)
    try:
        from app.services.weather_spot_service import get_or_create_weather_spot_assets

        time_asset, weather_asset = await get_or_create_weather_spot_assets(db, slot_key)
    except Exception:
        logger.warning("Failed to create weather spot assets for slot %s", slot_key, exc_info=True)
        return

    if not time_asset and not weather_asset:
        logger.warning("No weather/time assets generated for slot %s", slot_key)
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

    # Use the exact 15-min boundary as preempt time so time announcements play on time
    boundary = now.replace(second=0, microsecond=0)
    for i, asset in enumerate(assets_to_insert):
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=station_id,
            asset_id=asset.id,
            position=next_pos + i,
            status="pending",
            preempt_at=boundary if i == 0 else None,  # preempt for time announcement only
        )
        db.add(entry)

    await db.flush()
    logger.info("Inserted weather spot for slot %s (%d items, preempt_at=%s)", slot_key, len(assets_to_insert), boundary.isoformat())


async def _replenish_queue(db: AsyncSession, station_id: uuid.UUID) -> None:
    """Auto-fill queue to ~24 hours of content using schedule rules."""
    from app.services.queue_replenish_service import QueueReplenishService
    
    service = QueueReplenishService(db, station_id)
    await service.replenish()


async def _check_advance(db: AsyncSession, station_id: uuid.UUID) -> QueueEntry | None:
    """Core playback engine: check if current track is done and auto-advance."""
    is_blackout = await _is_blacked_out(db, station_id)

    # Hourly jingles and weather are now pre-scheduled via _schedule_hourly_announcements()
    # in queue_replenish_service.py with exact preempt_at timestamps.
    # The old real-time insertion functions are disabled to prevent duplicates.

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

    # Check if a pending entry needs to preempt the current track (exact-time playback)
    now_utc = datetime.now(timezone.utc)
    preempt_result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now_utc,
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
        .limit(1)
    )
    preempt_entry = preempt_result.scalar_one_or_none()
    if preempt_entry:
        # Don't preempt silence with more silence — let it finish naturally
        if current.asset_id != preempt_entry.asset_id:
            # Stop current track and start the preempt entry immediately
            if current.started_at:
                log = PlayLog(
                    id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                    start_utc=current.started_at, end_utc=now_utc, source="scheduler",
                )
                db.add(log)
            current.status = "played"
            preempt_entry.status = "playing"
            preempt_entry.started_at = now_utc
            await db.commit()
            logger.info("Preempted current track for time-critical entry %s", preempt_entry.id)
            return preempt_entry

    # Check if track duration has elapsed
    asset = current.asset
    duration = asset.duration if asset else 0
    if not duration:
        duration = 180  # default 3 min

    # Blackout ended while silence is playing — stop silence immediately
    if not is_blackout and asset and asset.asset_type == "silence":
        if current.started_at:
            log = PlayLog(
                id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                start_utc=current.started_at, end_utc=now_utc, source="scheduler",
            )
            db.add(log)
        current.status = "played"
        # Mark ALL remaining pending silence entries as played
        await db.execute(
            update(QueueEntry).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id == current.asset_id,
                QueueEntry.status == "pending",
            ).values(status="played")
        )
        logger.info("Blackout ended — cleared remaining silence entries for station %s", station_id)
        # Fall through to find the next real song below

    else:
        elapsed = (now_utc - current.started_at).total_seconds()
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

    # Find next pending (skip entries with preempt_at in the future)
    result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now_utc),
        )
        .order_by(QueueEntry.position)
        .limit(1)
    )
    next_entry = result.scalar_one_or_none()
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)

        # Compact positions: ensure playing entry is at position 1
        # and pending entries follow sequentially (prevents stale position drift)
        compact_result = await db.execute(
            select(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
            .order_by(
                # Playing first, then pending by position
                QueueEntry.status.desc(),  # "playing" > "pending" alphabetically
                QueueEntry.position,
            )
        )
        for idx, entry in enumerate(compact_result.scalars().all()):
            entry.position = idx + 1

        await db.commit()
        # Replenish AFTER commit so the next song starts immediately (skip during blackout)
        if not is_blackout:
            try:
                await _replenish_queue(db, station_id)
            except Exception as e:
                logger.warning("Queue replenish failed (non-critical): %s", e)
        return next_entry

    await db.commit()
    return None


@router.get("")
async def get_queue(
    station_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Pure read-only — advancement is handled by the background scheduler.
    import traceback as _tb
    from sqlalchemy.orm import joinedload

    try:
      return await _get_queue_impl(station_id, limit, db)
    except Exception as exc:
        logger.exception("Queue GET error for station %s: %s", station_id, exc)
        return {"entries": [], "total": 0, "now_playing": None,
                "queue_duration_seconds": 0, "preempt_fade_ms": 2000,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": _tb.format_exc()}


async def _get_queue_impl(station_id, limit, db):
    from sqlalchemy.orm import joinedload

    result = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )
    entries = list(result.unique().scalars().all())

    # Find now-playing from the fetched entries (no extra query)
    now_playing_entry = next((e for e in entries if e.status == "playing"), None)

    # Sort ALL entries by estimated play time (chronological order)
    try:
        now_utc_sort = datetime.now(timezone.utc)
        _np_sa = now_playing_entry.started_at if now_playing_entry and now_playing_entry.started_at else None
        if _np_sa and _np_sa.tzinfo is None:
            _np_sa = _np_sa.replace(tzinfo=timezone.utc)

        if _np_sa:
            _ad = now_playing_entry.asset.duration if now_playing_entry.asset else DEFAULT_DURATION
            _el = (now_utc_sort - _np_sa).total_seconds()
            _sort_cursor = now_utc_sort + timedelta(seconds=max(0, (_ad or DEFAULT_DURATION) - _el))
        else:
            _sort_cursor = now_utc_sort

        _est_map = {}
        for e in entries:
            if e.status == "playing":
                _est_map[e.id] = _np_sa or now_utc_sort
            elif e.preempt_at:
                pa = e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=timezone.utc)
                _est_map[e.id] = pa
            else:
                _est_map[e.id] = _sort_cursor
                dur = e.asset.duration if e.asset and e.asset.duration else DEFAULT_DURATION
                _sort_cursor += timedelta(seconds=dur)

        entries.sort(key=lambda e: (0 if e.status == "playing" else 1, _est_map.get(e.id, now_utc_sort)))
        entries = entries[:limit]
    except Exception as _sort_err:
        logger.exception("Queue sort error: %s", _sort_err)
        # Fallback: simple position sort with limit
        entries.sort(key=lambda e: (0 if e.status == "playing" else 1, e.position))
        entries = entries[:limit]

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
            "preempt_at": now_playing_entry.preempt_at.isoformat() if now_playing_entry.preempt_at else None,
            "asset": now_playing_entry.asset,
            "started_at": now_playing_entry.started_at.isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "remaining_seconds": round(remaining, 1),
        }

    # Calculate estimated start time for each entry
    now_utc = datetime.now(timezone.utc)
    if now_playing_entry and now_playing_entry.started_at:
        asset_dur = now_playing_entry.asset.duration if now_playing_entry.asset else DEFAULT_DURATION
        el = (now_utc - now_playing_entry.started_at).total_seconds()
        cursor = now_utc + timedelta(seconds=max(0, (asset_dur or DEFAULT_DURATION) - el))
    else:
        cursor = now_utc

    # Find blackout windows so we can cap silence blocks precisely + show labels
    blackout_ends = {}  # bo_start -> bo_end datetime
    blackout_names = {}  # bo_start -> window name
    silence_asset_id = None
    for e in entries:
        if e.asset and e.asset.asset_type == "silence":
            silence_asset_id = e.asset_id
            break
    if silence_asset_id:
        bo_stmt = select(HolidayWindow).where(
            HolidayWindow.is_blackout == True,
            HolidayWindow.end_datetime > now_utc,
        ).order_by(HolidayWindow.start_datetime)
        bo_result = await db.execute(bo_stmt)
        for w in bo_result.scalars().all():
            if w.affected_stations is None or str(station_id) in [str(s) for s in (w.affected_stations or {}).get("station_ids", [])]:
                bo_end = w.end_datetime
                if bo_end.tzinfo is None:
                    bo_end = bo_end.replace(tzinfo=timezone.utc)
                bo_start = w.start_datetime
                if bo_start.tzinfo is None:
                    bo_start = bo_start.replace(tzinfo=timezone.utc)
                blackout_ends[bo_start] = bo_end
                blackout_names[bo_start] = w.name

    entries_data = []
    queue_duration = 0.0
    in_silence_block = False
    current_blackout_end = None
    current_blackout_name = None

    # If currently playing silence (active blackout), find its blackout end time
    if now_playing_entry and now_playing_entry.asset and now_playing_entry.asset.asset_type == "silence":
        for bo_start, bo_end in blackout_ends.items():
            if bo_start <= now_utc:
                current_blackout_end = bo_end
                current_blackout_name = blackout_names.get(bo_start)
                in_silence_block = True
                break

    for e in entries:
        dur = (e.asset.duration if e.asset and e.asset.duration else DEFAULT_DURATION)
        queue_duration += dur
        is_now = e.status == "playing"
        is_silence = e.asset and e.asset.asset_type == "silence"

        # If entry has preempt_at in the future, it will start at that time
        if not is_now and e.preempt_at:
            pa = e.preempt_at
            if pa.tzinfo is None:
                pa = pa.replace(tzinfo=timezone.utc)
            if pa > cursor:
                cursor = pa
            # Find which blackout this belongs to
            if not current_blackout_end:
                for bo_start, bo_end in blackout_ends.items():
                    if abs((pa - bo_start).total_seconds()) < 60:
                        current_blackout_end = bo_end
                        current_blackout_name = blackout_names.get(bo_start)
                        break

        if is_silence:
            in_silence_block = True
            # If we don't have a name yet (active blackout, no preempt_at), try to find it
            if not current_blackout_name:
                for bo_start, bo_end in blackout_ends.items():
                    if bo_start <= now_utc and bo_end > now_utc:
                        current_blackout_name = blackout_names.get(bo_start)
                        break
        elif in_silence_block:
            # First non-silence entry after silence block — cap cursor to blackout end
            in_silence_block = False
            if current_blackout_end and current_blackout_end < cursor:
                cursor = current_blackout_end
            current_blackout_end = None
            current_blackout_name = None

        est_start = now_playing_entry.started_at.isoformat() if is_now and now_playing_entry and now_playing_entry.started_at else cursor.isoformat()
        d = {
            "id": str(e.id),
            "station_id": str(e.station_id),
            "asset_id": str(e.asset_id),
            "position": e.position,
            "status": e.status,
            "estimated_start": est_start,
            "preempt_at": e.preempt_at.isoformat() if e.preempt_at else None,
            "asset": {
                "id": str(e.asset.id),
                "title": e.asset.title,
                "artist": e.asset.artist,
                "duration": e.asset.duration,
                "asset_type": e.asset.asset_type,
                "category": e.asset.category,
            } if e.asset else None,
        }
        if is_silence and current_blackout_name:
            d["blackout_name"] = current_blackout_name
        if not is_now:
            cursor += timedelta(seconds=dur)
        entries_data.append(d)

    return {
        "entries": entries_data,
        "total": len(entries_data),
        "now_playing": np_data,
        "queue_duration_seconds": round(queue_duration, 1),
        "preempt_fade_ms": 2000,
    }


@router.get("/log")
async def get_play_log(
    station_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get recent play history."""
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(PlayLog)
        .options(selectinload(PlayLog.asset))
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
    _user: User = Depends(require_dj_or_manager),
):
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )
    max_pos = result.scalar() or 0
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=body.asset_id,
        position=max_pos + 1, status="pending", source="manual",
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
    _user: User = Depends(require_dj_or_manager),
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
            position=max_pos, status="pending", source="manual",
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
    _user: User = Depends(require_dj_or_manager),
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
        position=next_pos, status="pending", source="manual",
    )
    db.add(entry)
    await db.commit()
    return {"id": str(entry.id), "position": entry.position, "message": "Queued as next"}


@router.post("/skip")
async def skip_current(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_dj_or_manager),
):
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    entry.position = body.new_position
    await db.commit()
    return {"message": "Reordered"}


async def _validate_queue_move(
    db: AsyncSession,
    station_id: uuid.UUID,
    moved_entry: QueueEntry,
    new_position: int,
    pending_entries: list[QueueEntry],
) -> list[str]:
    """Check schedule rules and category transitions for a queue move.

    Returns a list of human-readable warning strings (empty if no conflicts).
    """
    from app.models.category import Category
    from app.models.schedule_rule import ScheduleRule

    warnings: list[str] = []
    moved_asset = moved_entry.asset
    if not moved_asset:
        return warnings

    # Build ordered list after the move
    ordered = [e for e in pending_entries if e.id != moved_entry.id]
    insert_idx = 0
    for i, e in enumerate(ordered):
        if e.position >= new_position:
            insert_idx = i
            break
    else:
        insert_idx = len(ordered)
    ordered.insert(insert_idx, moved_entry)

    # ── 1. Category transition checks ──
    prev_asset = ordered[insert_idx - 1].asset if insert_idx > 0 else None
    next_asset = ordered[insert_idx + 1].asset if insert_idx < len(ordered) - 1 else None

    if moved_asset.category:
        # Load category record for allowed_transitions
        result = await db.execute(
            select(Category).where(Category.name == moved_asset.category)
        )
        cat_record = result.scalar_one_or_none()
        if cat_record and cat_record.allowed_transitions:
            allowed = cat_record.allowed_transitions  # e.g. {"after": ["relax","med_fast"], "before": ["lively"]}
            if isinstance(allowed, dict):
                allowed_after = allowed.get("after")  # categories that can precede this one
                allowed_before = allowed.get("before")  # categories that can follow this one
                if allowed_after and prev_asset and prev_asset.category:
                    if prev_asset.category not in allowed_after:
                        warnings.append(
                            f"Category transition conflict: \"{moved_asset.category}\" should not follow \"{prev_asset.category}\""
                        )
                if allowed_before and next_asset and next_asset.category:
                    if next_asset.category not in allowed_before:
                        warnings.append(
                            f"Category transition conflict: \"{next_asset.category}\" should not follow \"{moved_asset.category}\""
                        )

    # ── 2. Daypart / schedule rule checks ──
    # Estimate when this entry will play based on position in queue
    now = datetime.now(timezone.utc)
    cumulative_duration = 0.0
    for e in ordered:
        if e.id == moved_entry.id:
            break
        dur = e.asset.duration if e.asset and e.asset.duration else DEFAULT_DURATION
        cumulative_duration += dur

    estimated_play_time = now + timedelta(seconds=cumulative_duration)
    est_hour = estimated_play_time.hour
    est_day = estimated_play_time.weekday()

    # Check active daypart rules
    result = await db.execute(
        select(ScheduleRule).where(
            ScheduleRule.is_active == True,
            ScheduleRule.rule_type == "daypart",
            ScheduleRule.hour_start <= est_hour,
            ScheduleRule.hour_end > est_hour,
        ).order_by(ScheduleRule.priority.desc())
    )
    daypart_rules = result.scalars().all()
    daypart_rules = [
        r for r in daypart_rules
        if str(est_day) in (r.days_of_week or "0,1,2,3,4,5,6").split(",")
    ]

    for rule in daypart_rules:
        # Check asset type mismatch
        if rule.asset_type and moved_asset.asset_type != rule.asset_type:
            warnings.append(
                f"Daypart rule \"{rule.name}\" expects {rule.asset_type} during {rule.hour_start}:00-{rule.hour_end}:00, "
                f"but this asset is {moved_asset.asset_type}"
            )
        # Check category mismatch
        if rule.category and moved_asset.category and moved_asset.category != rule.category:
            warnings.append(
                f"Daypart rule \"{rule.name}\" expects category \"{rule.category}\" during {rule.hour_start}:00-{rule.hour_end}:00, "
                f"but this asset is \"{moved_asset.category}\""
            )

    # ── 3. Do-not-play check ──
    if moved_asset.category == "do_not_play":
        warnings.append("This asset is marked \"do_not_play\" and should not be in the queue")

    return warnings


@router.post("/reorder-dnd")
async def reorder_dnd(
    station_id: uuid.UUID,
    body: QueueDndReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Drag-and-drop reorder: move entry to new position with rule validation."""
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry or entry.status != "pending":
        raise NotFoundError("Entry not found or not pending")

    # Fetch all pending entries in order
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
    )
    pending = list(result.scalars().all())

    # Validate the move against rules
    warnings = await _validate_queue_move(db, station_id, entry, body.new_position, pending)

    # Perform the move: remove entry from old position, shift others, insert at new
    old_pos = entry.position
    new_pos = body.new_position

    if old_pos != new_pos:
        if old_pos < new_pos:
            # Moving down: shift entries between old+1..new up by 1
            await db.execute(
                update(QueueEntry).where(
                    QueueEntry.station_id == station_id,
                    QueueEntry.status == "pending",
                    QueueEntry.position > old_pos,
                    QueueEntry.position <= new_pos,
                ).values(position=QueueEntry.position - 1)
            )
        else:
            # Moving up: shift entries between new..old-1 down by 1
            await db.execute(
                update(QueueEntry).where(
                    QueueEntry.station_id == station_id,
                    QueueEntry.status == "pending",
                    QueueEntry.position >= new_pos,
                    QueueEntry.position < old_pos,
                ).values(position=QueueEntry.position + 1)
            )
        entry.position = new_pos
        await db.commit()

    return {"message": "Reordered", "warnings": warnings}


@router.delete("/{entry_id}", status_code=204)
async def remove_from_queue(
    station_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_dj_or_manager),
):
    # During blackout, fill queue with silence entries
    if await _is_blacked_out(db, station_id):
        count = await fill_blackout_queue(db, station_id)
        silence = await db.execute(select(Asset).where(Asset.asset_type == "silence").limit(1))
        silence_asset = silence.scalar_one_or_none()
        sid = str(silence_asset.id) if silence_asset else None
        return {"message": f"Blackout active — {count} silence entries queued", "now_playing": sid}

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
    try:
        await _replenish_queue(db, station_id)
    except Exception as exc:
        logger.error("_replenish_queue failed during start: %s", exc, exc_info=True)

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
    # Round down to current hour
    slot_key = eastern_now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H")

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


@router.post("/insert-weather")
async def insert_weather_now(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Force-insert time announcement + weather spot into queue (admin trigger)."""
    if not settings.elevenlabs_enabled or not settings.supabase_storage_enabled:
        return JSONResponse({"error": "ElevenLabs or Supabase storage not configured"}, status_code=503)

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    now = datetime.now(timezone.utc)
    eastern_now = now.astimezone(ZoneInfo("America/New_York"))
    slot_key = eastern_now.strftime("%Y-%m-%dT%H")

    from app.services.weather_spot_service import get_or_create_weather_spot_assets
    time_asset, weather_asset = await get_or_create_weather_spot_assets(db, slot_key)

    assets_to_insert = [a for a in [time_asset, weather_asset] if a is not None]
    if not assets_to_insert:
        return {"message": "No weather/time assets generated", "inserted": 0}

    # Bump pending positions and insert at front
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
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id,
            position=next_pos + i, status="pending",
        )
        db.add(entry)

    await db.commit()
    return {"message": f"Inserted {len(assets_to_insert)} weather/time assets", "inserted": len(assets_to_insert)}


@router.post("/schedule-hourly")
async def schedule_hourly(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Force-run hourly announcement scheduling for this station."""
    from app.services.queue_replenish_service import QueueReplenishService

    service = QueueReplenishService(db, station_id)
    await service._load_automation_config()

    # Set max_pos
    result = await db.execute(
        select(func.coalesce(func.max(QueueEntry.position), 0))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )
    service.max_pos = result.scalar() or 0

    try:
        await service._schedule_hourly_announcements()
        await db.commit()
    except Exception as exc:
        logger.error("schedule-hourly failed: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)

    # Count how many preempt entries exist now
    result = await db.execute(
        select(func.count())
        .select_from(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
        )
    )
    count = result.scalar() or 0
    return {"message": f"Hourly scheduling complete", "preempt_entries": count}
