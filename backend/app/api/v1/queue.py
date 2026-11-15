import asyncio
import json
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.core.dependencies import (
    require_dj_or_manager,
    require_manager,
    require_staff,
    require_station_operator,
)
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry, hard_timed, is_soft_timed, soft_timed
from app.models.user import User
from app.schemas.queue import (
    QueueAdd,
    QueueBulkAdd,
    QueueDndReorder,
    QueueFireNow,
    QueueInsert,
    QueueLoop,
    QueueMixOver,
    QueueReorder,
)

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
            el = (now - _aware(playing_entry.started_at)).total_seconds()
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

router = APIRouter(
    prefix="/stations/{station_id}/queue",
    tags=["queue"],
    dependencies=[Depends(require_station_operator)],
)


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


# Next pressed while the automatic change to the next song is still fading in must not
# skip that song too: for this long after an automatic change, Next keeps the incoming song.
NEXT_KEEPS_INCOMING_SECONDS = 4.0
# station id -> (entry that just went on air by itself, monotonic time). In memory: a
# restart only means one Next in that moment behaves the old way.
_automatic_change: dict[str, tuple[str, float]] = {}


def _note_automatic_change(station_id, entry_id) -> None:
    _automatic_change[str(station_id)] = (str(entry_id), time.monotonic())


def _incoming_from_automatic_change(station_id, entry_id) -> bool:
    """True once per automatic change, while that change is still fading in."""
    noted = _automatic_change.pop(str(station_id), None)
    return bool(
        noted
        and noted[0] == str(entry_id)
        and time.monotonic() - noted[1] < NEXT_KEEPS_INCOMING_SECONDS
    )


# ── Loop: the item on air plays again each time it ends ──────────────────────
# Kept in the station's automation_config ("loop_asset_id") so it survives a restart.
# Each repeat is a real queue entry with source "loop", added just before it is needed.


async def loop_asset_id(db: AsyncSession, station_id) -> str | None:
    from app.models.station import Station

    res = await db.execute(select(Station.automation_config).where(Station.id == station_id))
    config = res.scalar_one_or_none() or {}
    return config.get("loop_asset_id") or None


async def _set_loop(db: AsyncSession, station_id, asset_id) -> None:
    from app.models.station import Station

    res = await db.execute(select(Station).where(Station.id == station_id))
    station = res.scalar_one_or_none()
    if station is None:
        raise NotFoundError("Station not found")
    config = dict(station.automation_config or {})
    if asset_id is None:
        config.pop("loop_asset_id", None)
    else:
        config["loop_asset_id"] = str(asset_id)
    station.automation_config = config


async def add_loop_repeat(db: AsyncSession, station_id, ignore_ids: set[str] | None = None) -> None:
    """If the station is looping and no repeat is waiting, queue one to play next.
    `ignore_ids`: repeats already handed to the playout engine (they are on their way to air)."""
    asset_id = await loop_asset_id(db, station_id)
    if not asset_id:
        return
    res = await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == "loop",
        )
    )
    waiting = {str(x) for x in res.scalars().all()} - (ignore_ids or set())
    if waiting:
        return
    res = await db.execute(
        select(func.min(QueueEntry.position)).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "pending"
        )
    )
    top = res.scalar()
    if top is None:
        res = await db.execute(
            select(func.max(QueueEntry.position)).where(QueueEntry.station_id == station_id)
        )
        top = (res.scalar() or 0) + 1
    else:
        await db.execute(
            update(QueueEntry)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
            .values(position=QueueEntry.position + 1)
        )
    db.add(
        QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=uuid.UUID(asset_id),
            position=top, status="pending", source="loop",
        )
    )
    await db.flush()


async def stop_loop(db: AsyncSession, station_id) -> bool:
    """Switch looping off and drop a repeat that has not aired. True if it was on."""
    if not await loop_asset_id(db, station_id):
        return False
    await _set_loop(db, station_id, None)
    res = await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == "loop",
        )
    )
    for entry in res.scalars().all():
        await db.delete(entry)
    await db.flush()
    return True


def _aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; they are UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Recent fire-now requests by the client's request id, so a double tap fires once
FIRE_REPEAT_WINDOW_SECONDS = 5.0
_fire_requests: dict[tuple[str, str], tuple[float, dict]] = {}


def _recent_fire_request(station_id, request_id: str) -> dict | None:
    now = time.monotonic()
    for key in [k for k, (at, _) in _fire_requests.items() if now - at > FIRE_REPEAT_WINDOW_SECONDS]:
        _fire_requests.pop(key, None)
    hit = _fire_requests.get((str(station_id), request_id))
    return hit[1] if hit else None


def _remember_fire_request(station_id, request_id: str | None, response: dict) -> None:
    if request_id:
        _fire_requests[(str(station_id), request_id)] = (time.monotonic(), response)


_advance_locks: dict[str, asyncio.Lock] = {}


def advance_lock(station_id) -> asyncio.Lock:
    """One track change at a time per station.

    The 3s poll, the precise timer and the Next button each use their own DB session.
    Without this, two of them can read the same playing entry at the same moment and
    each promote a different pending entry: one item is marked playing, replaced a
    moment later and never heard.
    """
    key = str(station_id)
    lock = _advance_locks.get(key)
    if lock is None:
        lock = _advance_locks[key] = asyncio.Lock()
    return lock


async def _check_advance(
    db: AsyncSession, station_id: uuid.UUID, early_seconds: float = 0.0
) -> QueueEntry | None:
    """Core playback engine: check if current track is done and auto-advance.

    `early_seconds` lets the precise timer advance at the crossfade point instead of
    the very end of the track (see SchedulerEngine._precise_advance).
    """
    async with advance_lock(station_id):
        return await _check_advance_locked(db, station_id, early_seconds)


async def _check_advance_locked(
    db: AsyncSession, station_id: uuid.UUID, early_seconds: float = 0.0
) -> QueueEntry | None:
    is_blackout = await _is_blacked_out(db, station_id)

    # Hourly jingles and weather are now pre-scheduled via _schedule_hourly_announcements()
    # in queue_replenish_service.py with exact preempt_at timestamps.
    # The old real-time insertion functions are disabled to prevent duplicates.

    # populate_existing: this session may have loaded these rows before another
    # advance changed them; read their committed state, not the cached one.
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
        .execution_options(populate_existing=True)
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
            # ad_slots use soft preempt (wait for song end), unless they have
            # waited as long as their block allows (hard_by)
            or_(hard_timed(), QueueEntry.hard_by <= now_utc),
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
            # Skip in Liquidsoap to sync with preempt
            if settings.liquidsoap_enabled:
                try:
                    from app.services.liquidsoap_client import skip as ls_skip
                    await ls_skip()
                except Exception:
                    pass
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
        started = current.started_at
        if started.tzinfo is None:  # SQLite returns naive datetimes
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (now_utc - started).total_seconds()
        # Small tolerance so a timer that fires a few ms early still advances
        if elapsed < duration - max(0.0, early_seconds) - 0.05:
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
        # Looping: the same item goes in again as the next thing to play
        await add_loop_repeat(db, station_id)

    # Check for soft-preempt ad slots first (clock-based :15/:30/:45 ads)
    ad_result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            soft_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now_utc,
        )
        .order_by(QueueEntry.preempt_at)
        .limit(1)
    )
    next_entry = ad_result.scalar_one_or_none()

    if not next_entry:
        # Normal: find next by position (skip entries with preempt_at in the future)
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
        if current is not None:
            _note_automatic_change(station_id, next_entry.id)

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
    limit: int = Query(2000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    # Pure read-only — advancement is handled by the background scheduler.

    try:
        body = await _get_queue_impl(station_id, limit, db)
        # Thousands of plain entries: encode them directly. FastAPI's generic
        # encoder walks every value in Python and was a tenth of the server's CPU
        # with a few control rooms open. Only now_playing holds a database object.
        body["now_playing"] = jsonable_encoder(body["now_playing"])
        return Response(json.dumps(body, default=_json_default), media_type="application/json")
    except Exception as exc:
        logger.exception("Queue GET error for station %s: %s", station_id, exc)
        return {"entries": [], "total": 0, "now_playing": None,
                "queue_duration_seconds": 0, "preempt_fade_ms": 2000}


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return jsonable_encoder(value)


async def _get_queue_impl(station_id, limit, db):
    from types import SimpleNamespace

    from sqlalchemy.orm import noload

    # The control room polls this every few seconds for a queue that runs days
    # ahead (thousands of rows). Reading plain columns instead of ORM objects is
    # several times cheaper, and this read changes nothing, so it needs no
    # identity map. Only the columns used below are fetched.
    entry_cols = (
        QueueEntry.id, QueueEntry.station_id, QueueEntry.asset_id, QueueEntry.position,
        QueueEntry.status, QueueEntry.started_at, QueueEntry.preempt_at,
        QueueEntry.source, QueueEntry.timing_mode, QueueEntry.hard_by,
    )
    asset_cols = (
        Asset.id, Asset.title, Asset.artist, Asset.duration, Asset.intro_seconds,
        Asset.asset_type, Asset.category,
    )
    result = await db.execute(
        select(*entry_cols, *asset_cols)
        .select_from(QueueEntry)
        .outerjoin(Asset, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )
    n = len(entry_cols)
    entry_names = [c.key for c in entry_cols]
    asset_names = [c.key for c in asset_cols]
    entries = []
    for row in result.all():
        e = SimpleNamespace(**dict(zip(entry_names, row[:n])))
        e.asset = SimpleNamespace(**dict(zip(asset_names, row[n:]))) if row[n] is not None else None
        entries.append(e)
    # The now-playing block returns the whole asset record: load that one fully.
    for e in entries:
        if e.status == "playing" and e.asset is not None:
            e.asset = (
                await db.execute(
                    select(Asset).options(noload(Asset.categories)).where(Asset.id == e.asset.id)
                )
            ).scalar_one()

    # Compute total queue duration from ALL entries (before limit truncation)
    total_queue_duration = sum(
        (e.asset.duration if e.asset and e.asset.duration else DEFAULT_DURATION)
        for e in entries
    )
    total_queue_entries = len(entries)

    # Find now-playing from the fetched entries (no extra query)
    now_playing_entry = next((e for e in entries if e.status == "playing"), None)

    # Sort entries by simulating actual playback order:
    # - Regular songs play sequentially by position
    # - Hard preempts (hourly time/weather) interrupt at their exact preempt_at
    # - Soft preempts (ad_slots) play after current song finishes when their time arrives
    try:
        now_utc_sort = datetime.now(timezone.utc)
        _np_sa = now_playing_entry.started_at if now_playing_entry and now_playing_entry.started_at else None
        if _np_sa and _np_sa.tzinfo is None:
            _np_sa = _np_sa.replace(tzinfo=timezone.utc)

        if _np_sa:
            _ad = now_playing_entry.asset.duration if now_playing_entry.asset else DEFAULT_DURATION
            _el = (now_utc_sort - _np_sa).total_seconds()
            _cursor = now_utc_sort + timedelta(seconds=max(0, (_ad or DEFAULT_DURATION) - _el))
        else:
            _cursor = now_utc_sort

        def _tz(dt):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        # Separate into groups
        playing = [e for e in entries if e.status == "playing"]
        regulars = sorted(
            [e for e in entries if e.status == "pending" and not e.preempt_at],
            key=lambda e: e.position,
        )
        hard_preempts = sorted(
            [e for e in entries if e.status == "pending" and e.preempt_at and not is_soft_timed(e)],
            key=lambda e: _tz(e.preempt_at),
        )
        soft_preempts = sorted(
            [e for e in entries if e.status == "pending" and e.preempt_at and is_soft_timed(e)],
            key=lambda e: _tz(e.preempt_at),
        )

        _est_map = {}
        for e in playing:
            _est_map[e.id] = _np_sa or now_utc_sort

        ri, hi, si = 0, 0, 0
        _safety = len(entries) * 3  # prevent infinite loops
        while (ri < len(regulars) or hi < len(hard_preempts) or si < len(soft_preempts)) and _safety > 0:
            _safety -= 1
            # 1. Fire hard preempt if its time has arrived
            if hi < len(hard_preempts):
                hp_pa = _tz(hard_preempts[hi].preempt_at)
                if hp_pa <= _cursor:
                    _cursor = hp_pa
                    _est_map[hard_preempts[hi].id] = _cursor
                    _dur = hard_preempts[hi].asset.duration if hard_preempts[hi].asset else DEFAULT_DURATION
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    hi += 1
                    continue

            # 2. Fire soft preempt (ad_slot) if its time has arrived
            if si < len(soft_preempts):
                sp_pa = _tz(soft_preempts[si].preempt_at)
                if sp_pa <= _cursor:
                    _est_map[soft_preempts[si].id] = _cursor
                    _dur = soft_preempts[si].asset.duration if soft_preempts[si].asset else DEFAULT_DURATION
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    si += 1
                    continue

            # 3. Play next regular song
            if ri < len(regulars):
                song = regulars[ri]
                song_dur = song.asset.duration if song.asset and song.asset.duration else DEFAULT_DURATION
                song_end = _cursor + timedelta(seconds=song_dur)

                # Check if hard preempt fires during this song
                if hi < len(hard_preempts):
                    hp_pa = _tz(hard_preempts[hi].preempt_at)
                    if hp_pa < song_end:
                        _est_map[song.id] = _cursor
                        _cursor = hp_pa  # preempt will fire next iteration
                        ri += 1
                        continue

                _est_map[song.id] = _cursor
                _cursor += timedelta(seconds=song_dur)
                ri += 1
            else:
                # No regular songs left — process remaining preempts by time
                next_hp = _tz(hard_preempts[hi].preempt_at) if hi < len(hard_preempts) else None
                next_sp = _tz(soft_preempts[si].preempt_at) if si < len(soft_preempts) else None
                if next_hp and (not next_sp or next_hp <= next_sp):
                    _cursor = max(_cursor, next_hp)
                    _est_map[hard_preempts[hi].id] = _cursor
                    _dur = hard_preempts[hi].asset.duration if hard_preempts[hi].asset else DEFAULT_DURATION
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    hi += 1
                elif next_sp:
                    _cursor = max(_cursor, next_sp)
                    _est_map[soft_preempts[si].id] = _cursor
                    _dur = soft_preempts[si].asset.duration if soft_preempts[si].asset else DEFAULT_DURATION
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    si += 1
                else:
                    break

        entries.sort(key=lambda e: (0 if e.status == "playing" else 1, _est_map.get(e.id, now_utc_sort)))
        entries = entries[:limit]
    except Exception as _sort_err:
        logger.exception("Queue sort error: %s", _sort_err)
        entries.sort(key=lambda e: (0 if e.status == "playing" else 1, e.position))
        entries = entries[:limit]

    # Calculate elapsed/remaining for now playing
    np_data = None
    if now_playing_entry and now_playing_entry.started_at:
        asset = now_playing_entry.asset
        duration = asset.duration if asset else 180
        elapsed = (datetime.now(timezone.utc) - _aware(now_playing_entry.started_at)).total_seconds()
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

    # Use _est_map for estimated start times (calculated by playback simulation above)
    now_utc = datetime.now(timezone.utc)
    # Fallback cursor only used if _est_map is missing an entry
    if now_playing_entry and now_playing_entry.started_at:
        asset_dur = now_playing_entry.asset.duration if now_playing_entry.asset else DEFAULT_DURATION
        el = (now_utc - _aware(now_playing_entry.started_at)).total_seconds()
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

        # Use simulated estimated time from _est_map (accurate play order)
        if is_now and now_playing_entry and now_playing_entry.started_at:
            est_start = now_playing_entry.started_at.isoformat()
        elif e.id in _est_map:
            est_start = _est_map[e.id].isoformat()
        else:
            est_start = cursor.isoformat()
            cursor += timedelta(seconds=dur)

        # Blackout label tracking
        if not is_now and e.preempt_at:
            pa = e.preempt_at
            if pa.tzinfo is None:
                pa = pa.replace(tzinfo=timezone.utc)
            if not current_blackout_end:
                for bo_start, bo_end in blackout_ends.items():
                    if abs((pa - bo_start).total_seconds()) < 60:
                        current_blackout_end = bo_end
                        current_blackout_name = blackout_names.get(bo_start)
                        break

        if is_silence:
            in_silence_block = True
            if not current_blackout_name:
                for bo_start, bo_end in blackout_ends.items():
                    if bo_start <= now_utc and bo_end > now_utc:
                        current_blackout_name = blackout_names.get(bo_start)
                        break
        elif in_silence_block:
            in_silence_block = False
            current_blackout_end = None
            current_blackout_name = None

        d = {
            "id": str(e.id),
            "station_id": str(e.station_id),
            "asset_id": str(e.asset_id),
            "position": e.position,
            "status": e.status,
            "estimated_start": est_start,
            "source": e.source,
            "preempt_at": e.preempt_at.isoformat() if e.preempt_at else None,
            # The control-room player fades the song only for these (controlroom/timing.ts)
            "hard_timed": bool(e.preempt_at) and not is_soft_timed(e),
            "hard_by": e.hard_by.isoformat() if e.hard_by else None,
            "asset": {
                "id": str(e.asset.id),
                "title": e.asset.title,
                "artist": e.asset.artist,
                "duration": e.asset.duration,
                "intro_seconds": e.asset.intro_seconds,
                "asset_type": e.asset.asset_type,
                "category": e.asset.category,
            } if e.asset else None,
        }
        if is_silence and current_blackout_name:
            d["blackout_name"] = current_blackout_name
        entries_data.append(d)

    return {
        "entries": entries_data,
        "total": len(entries_data),
        "total_all": total_queue_entries,
        "now_playing": np_data,
        "queue_duration_seconds": round(queue_duration, 1),
        "total_queue_duration_seconds": round(total_queue_duration, 1),
        "preempt_fade_ms": 2000,
        # Set while the operator has Loop on: this asset repeats until Loop off or Next
        "loop_asset_id": await loop_asset_id(db, station_id),
    }


@router.get("/log")
async def get_play_log(
    station_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
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
    _user: User = Depends(require_staff),
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


@router.post("/fire-now", status_code=201)
async def fire_now(
    station_id: uuid.UUID,
    body: QueueFireNow,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Put an asset on air now (one call instead of play-next + skip)."""
    if body.client_request_id:
        repeat = _recent_fire_request(station_id, body.client_request_id)
        if repeat is not None:
            return {**repeat, "duplicate": True}
    queued = await play_next(station_id, QueueAdd(asset_id=body.asset_id), db, _user)
    if body.mode == "after-current":
        done = {**queued, "message": "Plays after the current track", "on_air": False}
        _remember_fire_request(station_id, body.client_request_id, done)
        return done

    if settings.engine_drives(station_id):
        # The engine loads the file, fades the music out and starts it; the song that
        # was playing does not resume. The real start comes back as a now-playing update.
        from sqlalchemy.orm import joinedload
        from app.services import playout_engine
        res = await db.execute(
            select(QueueEntry).options(joinedload(QueueEntry.asset))
            .where(QueueEntry.id == uuid.UUID(queued["id"]))
        )
        entry = res.unique().scalar_one()
        uri = playout_engine.build_uri(entry, "timed")
        state = playout_engine.get_state(station_id)
        state.timed_sent.add(str(entry.id))
        if uri and await playout_engine.engine_timed(uri, time.time(), str(entry.id)):
            done = {**queued, "message": "Going on air", "on_air": True}
            _remember_fire_request(station_id, body.client_request_id, done)
            return done
        state.timed_sent.discard(str(entry.id))
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The playout engine did not respond. The item is queued as next.",
                "id": queued["id"],
            },
        )

    # The operator asked for this item: it goes on air even if a song change is in progress
    async with advance_lock(station_id):
        skipped = await _skip_current_locked(station_id, db, keep_incoming=False)
    done = {**queued, "message": "Going on air", "on_air": True, "queue": skipped.get("queue")}
    _remember_fire_request(station_id, body.client_request_id, done)
    return done


@router.post("/mix-over")
async def mix_over(
    station_id: uuid.UUID,
    body: QueueMixOver,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Play an asset on top of what is on air. The item on air carries on and the queue
    does not move."""
    from app.services import playout_engine

    if body.client_request_id:
        repeat = _recent_fire_request(station_id, "mix:" + body.client_request_id)
        if repeat is not None:
            return {**repeat, "duplicate": True}
    asset = await db.get(Asset, body.asset_id)
    if asset is None:
        raise NotFoundError("Asset not found")
    url = playout_engine.audio_url(asset)
    if not url:
        raise HTTPException(status_code=409, detail="This item has no audio file to play")

    if settings.engine_drives(station_id):
        if not await playout_engine.engine_overlay(asset):
            raise HTTPException(status_code=503, detail="The playout engine did not respond")
    else:
        # Browser playback: every open player mixes it in on top of its own audio
        from app.api.v1.websocket import manager as ws_manager

        analysis = (asset.metadata_extra or {}).get("audio_analysis", {}) if asset.metadata_extra else {}
        await ws_manager.broadcast_to_station(str(station_id), {
            "type": "mix_over",
            "data": {
                "station_id": str(station_id),
                "asset_id": str(asset.id),
                "title": asset.title,
                "url": url,
                "replay_gain_db": analysis.get("replay_gain_db"),
            },
        })

    now = datetime.now(timezone.utc)
    db.add(PlayLog(
        id=uuid.uuid4(), station_id=station_id, asset_id=asset.id, start_utc=now,
        end_utc=now + timedelta(seconds=float(asset.duration or 0)), source="manual",
    ))
    await db.commit()
    done = {"message": "Mixing over", "asset_id": str(asset.id), "on_air": True}
    _remember_fire_request(station_id, body.client_request_id and "mix:" + body.client_request_id, done)
    return done


# How long the outgoing item fades when the operator presses Next, by the button used
SKIP_FADE_SECONDS = {"hard": 0.0, "fade": 0.5, "soft": 3.0}


@router.post("/skip")
async def skip_current(
    station_id: uuid.UUID,
    fade: Literal["hard", "fade", "soft"] = Query("fade"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Next. `fade`: hard = cut, fade = quick fade (default), soft = long fade."""
    fade_seconds = SKIP_FADE_SECONDS[fade]
    if settings.engine_drives(station_id):
        # The playout engine fades out and starts the next track itself, then reports
        # the real start back (which updates the queue and every client).
        from app.services import playout_engine
        # Next ends a loop: the pre-loaded repeat must not come back
        if await stop_loop(db, station_id):
            await db.commit()
            await playout_engine.engine_flush(station_id)
        if await playout_engine.engine_skip(station_id, fade_seconds):
            return {"message": "Skipped", "now_playing": None, "fade_seconds": fade_seconds}
        return JSONResponse(
            status_code=503,
            content={"detail": "The playout engine did not respond. Nothing was skipped."},
        )

    # Same lock as the automatic advance, so Next cannot collide with a song change
    async with advance_lock(station_id):
        done = await _skip_current_locked(station_id, db, fade_seconds=fade_seconds)
    return {**done, "fade_seconds": fade_seconds}


async def _skip_current_locked(
    station_id: uuid.UUID,
    db: AsyncSession,
    fade_seconds: float | None = None,
    keep_incoming: bool = True,
):
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
        .execution_options(populate_existing=True)
    )
    playing_entries = result.scalars().all()
    current = playing_entries[0] if playing_entries else None
    if current and keep_incoming and _incoming_from_automatic_change(station_id, current.id):
        # The station is already changing to this song (it is fading in right now).
        # Skipping it as well would jump two songs on one press: keep it on air.
        return {
            "message": "Already changing to the next item",
            "now_playing": str(current.asset_id),
            "kept_incoming": True,
            "queue": await _queue_after_action(db, station_id),
        }
    # Next ends a loop
    await stop_loop(db, station_id)
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

    # Advance to next — same logic as _check_advance:
    # 1. Check for soft-preempt ad slots whose time has arrived
    # 2. Then find next regular entry (skip future preempts)
    now_skip = datetime.now(timezone.utc)
    ad_result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            soft_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now_skip,
        )
        .order_by(QueueEntry.preempt_at)
        .limit(1)
    )
    next_entry = ad_result.scalar_one_or_none()

    if not next_entry:
        result = await db.execute(
            select(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now_skip),
            )
            .order_by(QueueEntry.position)
            .limit(1)
        )
        next_entry = result.scalar_one_or_none()

    # Skip in Liquidsoap to keep it in sync
    if settings.liquidsoap_enabled:
        try:
            from app.services.liquidsoap_client import skip as ls_skip
            await ls_skip()
        except Exception:
            pass

    await _replenish_queue(db, station_id)
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)
        next_id = next_entry.id
        next_asset_id = next_entry.asset_id
        await db.commit()
        await _broadcast_now_playing_entry(db, station_id, next_id, fade_seconds)
        return {
            "message": "Skipped",
            "now_playing": str(next_asset_id),
            "queue": await _queue_after_action(db, station_id),
        }

    await db.commit()
    return {"message": "Queue empty", "now_playing": None, "queue": await _queue_after_action(db, station_id)}


async def _queue_after_action(db: AsyncSession, station_id) -> dict | None:
    """The same body as GET /queue, returned with an action's response so the
    client does not need a second request to show the result."""
    try:
        return await _get_queue_impl(station_id, 2000, db)
    except Exception as exc:
        logger.warning("Queue snapshot after action failed: %s", exc)
        return None


async def _broadcast_now_playing_entry(
    db: AsyncSession, station_id, entry_id, change_fade_seconds: float | None = None
) -> None:
    """Immediately push the new now-playing over WebSocket so clients switch
    without waiting for the scheduler's next poll cycle."""
    try:
        from sqlalchemy.orm import joinedload
        from app.services.scheduler_engine import get_scheduler
        res = await db.execute(
            select(QueueEntry)
            .options(joinedload(QueueEntry.asset))
            .where(QueueEntry.id == entry_id)
        )
        fresh = res.unique().scalar_one_or_none()
        if fresh:
            await get_scheduler()._broadcast_queue_entry(
                db, station_id, fresh, change_fade_seconds=change_fade_seconds
            )
    except Exception as e:
        logger.warning("Instant now-playing broadcast failed: %s", e)


@router.post("/loop")
async def loop_current(
    station_id: uuid.UUID,
    body: QueueLoop,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Loop on: the item on air plays again each time it ends. Off (or Next) ends it;
    the play in progress finishes normally."""
    async with advance_lock(station_id):
        if body.on:
            result = await db.execute(
                select(QueueEntry)
                .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
                .order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
            )
            current = result.scalar_one_or_none()
            if current is None:
                raise HTTPException(status_code=409, detail="Nothing is on air to loop")
            await _set_loop(db, station_id, current.asset_id)
        else:
            await stop_loop(db, station_id)
        await db.commit()
    if settings.engine_drives(station_id):
        # The engine has the following track pre-loaded: make it ask again
        from app.services import playout_engine
        await playout_engine.engine_flush(station_id)
    return {
        "loop": body.on,
        "asset_id": await loop_asset_id(db, station_id),
        "queue": await _queue_after_action(db, station_id),
    }


@router.post("/insert", status_code=201)
async def insert_into_queue(
    station_id: uuid.UUID,
    body: QueueInsert,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Insert assets, in order, right after a queue entry (or at the top). One call:
    the response carries the new queue (same body as GET /queue)."""
    asset_ids = ([body.asset_id] if body.asset_id else []) + list(body.asset_ids)
    if not asset_ids:
        raise HTTPException(status_code=422, detail="Give asset_id or asset_ids")
    found = await db.execute(select(Asset.id).where(Asset.id.in_(asset_ids)))
    known = set(found.scalars().all())
    if any(a not in known for a in asset_ids):
        raise NotFoundError("Asset not found")

    async with advance_lock(station_id):
        result = await db.execute(
            select(QueueEntry)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
        )
        current = result.scalar_one_or_none()
        after_pos = current.position if current else 0
        if body.after_entry_id:
            res = await db.execute(
                select(QueueEntry).where(
                    QueueEntry.id == body.after_entry_id, QueueEntry.station_id == station_id
                )
            )
            anchor = res.scalar_one_or_none()
            # An anchor that has already played or gone: insert at the top instead
            if anchor and anchor.status in ("pending", "playing"):
                after_pos = anchor.position
        await db.execute(
            update(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.position > after_pos,
            )
            .values(position=QueueEntry.position + len(asset_ids))
        )
        new_ids = []
        for i, asset_id in enumerate(asset_ids):
            entry = QueueEntry(
                id=uuid.uuid4(), station_id=station_id, asset_id=asset_id,
                position=after_pos + 1 + i, status="pending", source="manual",
            )
            db.add(entry)
            new_ids.append(str(entry.id))
        await db.commit()
    if settings.engine_drives(station_id):
        from app.services import playout_engine
        await playout_engine.engine_flush(station_id)
    return {
        "message": f"Inserted {len(new_ids)}",
        "ids": new_ids,
        "queue": await _queue_after_action(db, station_id),
    }


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

    # populate_existing: this session may have loaded these rows before another
    # advance changed them; read their committed state, not the cached one.
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
        .execution_options(populate_existing=True)
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

    if settings.engine_drives(station_id):
        # The engine pulls its next track by itself; just make sure there is something to pull.
        try:
            await _replenish_queue(db, station_id)
        except Exception as exc:
            logger.error("_replenish_queue failed during start: %s", exc, exc_info=True)
        return {"message": "Playout engine will start the next track", "now_playing": None}

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
    started_id = next_entry.id
    started_asset_id = next_entry.asset_id
    await db.commit()
    await _broadcast_now_playing_entry(db, station_id, started_id)
    return {"message": "Started", "now_playing": str(started_asset_id)}



@router.post("/preview-weather")
async def preview_weather(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
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
    return {"message": "Hourly scheduling complete", "preempt_entries": count}


@router.post("/force-replenish")
async def force_replenish(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Force a full queue replenish for debugging."""
    try:
        await _replenish_queue(db, station_id)
        await db.commit()
    except Exception as exc:
        logger.error("force-replenish failed: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)

    # Report stats
    result = await db.execute(
        select(func.count())
        .select_from(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
    )
    total_entries = result.scalar() or 0

    result2 = await db.execute(
        select(func.coalesce(func.sum(func.coalesce(Asset.duration, DEFAULT_DURATION)), 0))
        .select_from(QueueEntry)
        .join(Asset, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
    )
    total_seconds = float(result2.scalar() or 0)

    return {
        "message": "Replenish complete",
        "total_entries": total_entries,
        "total_seconds": total_seconds,
        "total_hours": round(total_seconds / 3600, 1),
        "target_seconds": 604800,
        "target_hours": 168.0,
    }
