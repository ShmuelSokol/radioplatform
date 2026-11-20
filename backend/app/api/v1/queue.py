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
from app.models.asset import Asset, fit_for_random_pick, stored_analysis
from app.models.play_log import PlayLog
from app.models.queue_entry import (
    RESUME_PREFIX,
    QueueEntry,
    aired_from,
    hard_timed,
    is_soft_timed,
    mark_started,
    resume_offset,
)
from app.models.user import User
from app.schemas.queue import (
    QueueAdd,
    QueueBulkAdd,
    QueueDndReorder,
    QueueFireNow,
    QueueInsert,
    QueueLoop,
    QueueMixOver,
    QueueModes,
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
    from app.services.station_service import window_covers_station

    for window in windows:
        if window_covers_station(window, station_id):
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
    silence_result = await db.execute(select(Asset).where(Asset.asset_type == "silence").order_by(Asset.created_at, Asset.id).limit(1))
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
            w_start, w_end = _aware(w.start_datetime), _aware(w.end_datetime)
            total += await _fill_single_blackout(db, station_id, w)
            try:
                if w_start <= now:
                    await _drop_leftovers(db, station_id, w_start, w_end)
                elif w_start - now <= SPOT_CUT_LOOKAHEAD:
                    await _drop_spots_cut_by(db, station_id, w_start)
            except Exception:
                logger.exception("Station %s: tidying the queue around the blackout failed", station_id)
                await db.rollback()

    # Compact positions after fill to prevent bloat from repeated bump operations
    if total > 0:
        from app.services.queue_positions import compact_positions

        await compact_positions(db, station_id, playing_first=False)
        await db.commit()
        logger.info("Compacted queue positions for station %s", station_id)

    return total


# How long before a blackout the item that would be cut by its silence is looked for
SPOT_CUT_LOOKAHEAD = timedelta(minutes=20)
# The queue's timeline runs a few seconds early (song ends are estimated): a spoken item
# must end this long before the blackout to stay. Main's TEST SPONSOR 29 (22 s) seemed to
# fit by under a second, started 4 s later than estimated and was cut at 19 s (review
# simulator run 2026-11-19).
SPOT_CUT_MARGIN = timedelta(seconds=8)
# What may never be cut off by the blackout's silence (music just fades, as at :00)
_SPOKEN_TYPES = (
    "spot", "jingle", "sweeper", "station_id", "time", "weather", "psa", "announcement",
    "zmanim", "sponsorship", "promo",
)


def _blackout_keeps(source) -> bool:
    from app.services.shabbos_ending import SOURCE as SHABBOS_END

    return source == SHABBOS_END


async def _drop_leftovers(db: AsyncSession, station_id, start, end) -> int:
    """Once a blackout has begun, what was queued for before it leaves the queue.

    The silences go on air at the window's start whatever is still waiting before them
    (they are hard timed). Songs, shiurim and sponsorships queued ahead of them stayed
    pending and aired first after Shabbos: Main's Friday-afternoon block, a sponsorship
    timed for 21:15 Friday and Jerusalem's Erev Shabbos daf ("Yoma 9") were lined up for
    Motzei Shabbos (queue read 2026-11-19 21:22Z). The window had moved earlier (candle
    lighting 14:19Z -> 13:57Z on Jerusalem) after those rows were drawn.

    Taken out: every waiting row other than silence that sits before the window's first
    waiting silence, and every waiting row timed before the window. Rows after the
    silences (what was drawn for after the window) and rows timed for after it stay."""
    silence_id = (await db.execute(
        select(Asset.id).where(Asset.asset_type == "silence").order_by(Asset.created_at, Asset.id).limit(1)
    )).scalar_one_or_none()
    if silence_id is None:
        return 0
    first_silence = (await db.execute(
        select(func.min(QueueEntry.position)).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.asset_id == silence_id,
        )
    )).scalar()
    rows = (await db.execute(
        select(QueueEntry.id, QueueEntry.position, QueueEntry.preempt_at, QueueEntry.source).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.asset_id != silence_id,
        )
    )).all()
    # What the engine holds pre-loaded for after the silence it is playing: drawn for
    # before the window, wherever it sits (simulator, Jerusalem 2026-11-20: the song cued
    # at 13:54:18Z stayed loaded through Shabbos and aired first after it)
    held: set[str] = set()
    engine = settings.engine_drives(station_id)
    if engine:
        from app.services import playout_engine

        state = playout_engine.get_state(station_id)
        held = {str(i) for i in ({state.cued_entry_id} | set(state.given)) if i}
    ids = []
    for rid, pos, pa, src in rows:
        if _blackout_keeps(src):
            continue
        if str(rid) in held and (pa is None or _aware(pa) < start):
            ids.append(rid)
            continue
        if pa is not None:
            if _aware(pa) < start:
                ids.append(rid)  # its time came before the window: it cannot air now
            continue  # timed inside the window (dropped already) or after it: stays
        if first_silence is not None and pos < first_silence:
            ids.append(rid)
    if not ids:
        return 0
    await db.execute(
        update(QueueEntry).where(QueueEntry.id.in_(ids)).values(status="skipped")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    logger.warning("Station %s: %d item(s) queued for before the blackout (from %s) left the queue",
                   station_id, len(ids), start)
    if engine and held & {str(i) for i in ids}:
        # the engine would play it when the silence ends: it asks again instead
        await playout_engine.engine_flush(station_id)
    return len(ids)


async def _drop_spots_cut_by(db: AsyncSession, station_id, start) -> int:
    """A sponsorship (or any spoken item) that would still be on air when the blackout
    starts is not started: the silence would cut it mid-sentence. Main's TEST SPONSOR 29
    (29 s) was estimated to start 7 s before Friday's silence (2026-11-19 21:22Z read).
    Worked out on the queue's own timeline in the last minutes before the window."""
    data = await _get_queue_impl(station_id, 500, db)
    ids = []
    for d in data.get("entries", []):
        if d.get("status") != "pending" or not d.get("estimated_start"):
            continue
        asset = d.get("asset") or {}
        if asset.get("asset_type") not in _SPOKEN_TYPES or _blackout_keeps(d.get("source")):
            continue
        est = _aware(datetime.fromisoformat(d["estimated_start"]))
        if est >= start:
            continue
        end = est + timedelta(seconds=float(asset.get("duration") or DEFAULT_DURATION))
        if end + SPOT_CUT_MARGIN > start:
            ids.append(d["id"])
    if not ids:
        return 0
    await db.execute(
        update(QueueEntry).where(QueueEntry.id.in_([uuid.UUID(i) for i in ids])).values(status="skipped")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    logger.warning("Station %s: %d spoken item(s) that the blackout at %s would cut left the queue",
                   station_id, len(ids), start)
    if settings.engine_drives(station_id):
        from app.services import playout_engine

        state = playout_engine.get_state(station_id)
        held = {str(i) for i in ({state.cued_entry_id} | set(state.given)) if i}
        if held & {str(i) for i in ids}:
            # already pre-loaded in the engine: it would still play it
            await playout_engine.engine_flush(station_id)
    return len(ids)


async def _drop_timed_inside(db: AsyncSession, station_id, silence_id, start, end) -> int:
    """Timed items (time check, weather, sponsorships, IDs) due inside a blackout leave
    the queue: nothing but silence goes on air on Shabbos / Yom Tov. They are queued
    before the window is known or before it moved: KBR Jerusalem's 16:00 time check
    (2026-11-20 14:00Z) was queued while candle lighting was still 14:19Z, and the
    window then moved to 13:57Z (40 minutes in Israel). The engine is handed timed
    items ahead of their time, so the blackout must not be left to the moment they air.
    The item at candle lighting (the shofar, shabbos_ending) stays."""
    from app.services.shabbos_ending import SOURCE as SHABBOS_END

    res = await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at >= start,
            QueueEntry.preempt_at < end,
            QueueEntry.asset_id != silence_id,
            or_(QueueEntry.source.is_(None), QueueEntry.source != SHABBOS_END),
        )
    )
    ids = list(res.scalars().all())
    if ids:
        await db.execute(
            update(QueueEntry).where(QueueEntry.id.in_(ids)).values(status="skipped")
        )
        await db.commit()
        logger.info("Station %s: %d timed item(s) inside the blackout from %s left the queue",
                    station_id, len(ids), start)
    return len(ids)


async def _fill_single_blackout(db: AsyncSession, station_id, window: "HolidayWindow"):
    """Fill queue with silence for a single blackout window."""
    import math

    now = datetime.now(timezone.utc)

    # Get silence asset
    silence_result = await db.execute(select(Asset).where(Asset.asset_type == "silence").order_by(Asset.created_at, Asset.id).limit(1))
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

    await _drop_timed_inside(db, station_id, silence.id, blackout_start, blackout_end)

    any_silence = select(Asset.id).where(Asset.asset_type == "silence")
    if is_active_now and settings.engine_drives(station_id):
        # Silence already on air on an engine station: never clear it and skip the engine.
        # /next hands only silence in a blackout and makes a row when none is left.
        # KBR Jerusalem 2026-11-20 ~17:30Z: this refill ran on every pass (its count came
        # up short), each time marking the silences played and skipping the engine, which
        # then raced through a silence every second or two.
        on_air = await db.execute(
            select(QueueEntry.id).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "playing",
                QueueEntry.asset_id.in_(any_silence),
            ).limit(1)
        )
        if on_air.first() is not None:
            return 0

    # Count existing silence entries for THIS specific blackout
    if is_active_now:
        # Active blackout: count silence without preempt_at or with past preempt_at
        # (of any silence item: with two in the library the count of one came up short)
        existing_silence = await db.execute(
            select(func.count(QueueEntry.id)).where(
                QueueEntry.station_id == station_id,
                QueueEntry.asset_id.in_(any_silence),
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

    # On a station the playout engine plays, the silence is queued and the engine is told
    # to move on (below): it reports the silence's start itself. Marking it playing here
    # left the song on air until it ended while the site showed silence (harness
    # blackout_ended_early: a blackout that starts now aired 10 s and more of music).
    engine = settings.engine_drives(station_id)
    start_now = is_active_now and not engine
    for i in range(num_silence):
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=station_id,
            asset_id=silence.id,
            position=insert_at + i + 1,
            status="playing" if (i == 0 and start_now) else "pending",
            started_at=now if (i == 0 and start_now) else None,
            preempt_at=blackout_start if not is_active_now else None,
            source="auto",
        )
        db.add(entry)

    await db.commit()
    if engine and is_active_now:
        from app.services import playout_engine

        await playout_engine.engine_flush(station_id)
        await playout_engine.engine_skip(station_id)
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


# Item types that must never be used to keep a station on the air
# A timed item due within this many seconds of a song change starts at the change
TIMED_EARLY_START_SECONDS = 8.0

_NOT_FILLER_TYPES = ("silence", "spot", "jingle", "sweeper", "station_id", "time", "weather")


# What an operator or listener chose themselves; a placeholder they pick still plays.
OPERATOR_SOURCES = ("manual", "request", "loop")


async def _skip_placeholders(db: AsyncSession, station_id) -> None:
    """Placeholder items ("KOL BRAMAH TEST SPONSOR", "Kol bramah ad"...) and items in
    category do_not_play never air from
    any automatic path. Rotations already avoid them; this catches every other way in
    (a playlist or block naming the item, a spot schedule, old queue rows): three of
    them aired on KBR Jerusalem at 18:56 ET on 2026-11-16."""
    from app.models.asset import not_placeholder

    from app.services.station_blocks import blocked_asset_ids

    ids = select(Asset.id).where(~not_placeholder()).scalar_subquery()
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            # ... or an item this station alone was told not to play
            or_(QueueEntry.asset_id.in_(ids), QueueEntry.asset_id.in_(blocked_asset_ids(station_id))),
            or_(QueueEntry.source.is_(None), QueueEntry.source.notin_(OPERATOR_SOURCES)),
        )
        .values(status="skipped")
        .execution_options(synchronize_session=False)
    )
    # A sponsorship still waiting in the queue on a day its contract no longer covers
    from app.services.spot_schedule_service import skip_close_repeats, skip_lapsed_sponsorships

    await skip_lapsed_sponsorships(db, station_id)
    # two waiting breaks that carry the same sponsorship never play back to back
    await skip_close_repeats(db, station_id)


async def _resume_before_soft(db: AsyncSession, station_id, entry: QueueEntry) -> QueueEntry:
    """A soft timed item that is due while a paused shiur or story waits to carry on
    plays after it, not in its pause: soft means "after the item on air", and a soft
    item still soft at this point was meant to wait for the shiur's end (less than
    five minutes left, or a sponsorship on a station where sponsorships wait). The
    simulator caught a 10:45 sponsorship airing in the 11:00 time check's pause of a
    story on All Day Stories (2026-11-19). Hard items still go first."""
    from app.services import long_items

    if not is_soft_timed(entry):
        return entry
    res = await db.execute(
        select(QueueEntry, Asset)
        .join(Asset, QueueEntry.asset_id == Asset.id)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source.like(f"{RESUME_PREFIX}%"),
            QueueEntry.position < entry.position,
        )
        .order_by(QueueEntry.position)
        .limit(1)
    )
    row = res.first()
    # Only a shiur or story: the rest of a song a live show paused still comes after
    # the sponsorship that waited for the show (test_live_delay)
    if row is None or not long_items.is_long_form(row[1]):
        return entry
    return row[0]


async def _pick_next_pending(
    db: AsyncSession, station_id, now_utc: datetime, silence_only: bool = False
) -> QueueEntry | None:
    """The next queue entry due to play: a timed item whose time has come (soft or
    hard), otherwise the first pending entry by position (future timed entries wait).

    Hard items count too: the weather that follows the hourly time check is due at
    the same moment but sits at the end of the queue by position. Picking by
    position started the next song when the time check ended, and the weather then
    cut it a moment later (heard live at 13:00 ET, 2026-11-16).

    `silence_only` (during a blackout): only the blackout's silence may start; a time
    check or anything else queued for those hours waits until it is over."""
    await _skip_placeholders(db, station_id)
    if silence_only:
        silence_ids = select(Asset.id).where(Asset.asset_type == "silence").scalar_subquery()
        res = await db.execute(
            select(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.asset_id.in_(silence_ids),
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now_utc),
            )
            .order_by(QueueEntry.preempt_at.nullsfirst(), QueueEntry.position)
            .limit(1)
        )
        return res.scalar_one_or_none()
    ad_result = await db.execute(
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
    entry = ad_result.scalar_one_or_none()
    if entry:
        return await _resume_before_soft(db, station_id, entry)
    # A timed item due in a moment starts now instead of waiting for the clock: the
    # song before it often ends a few seconds early, and waiting left that much quiet
    # air (heard live: 3.3 s before a block at 08:35:00). Starting the next song
    # instead would only have it cut off seconds later.
    soon = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at > now_utc,
            QueueEntry.preempt_at <= now_utc + timedelta(seconds=TIMED_EARLY_START_SECONDS),
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
        .limit(1)
    )
    entry = soon.scalar_one_or_none()
    if entry:
        return await _resume_before_soft(db, station_id, entry)
    from sqlalchemy.orm import joinedload

    from app.services.station_id_guard import must_wait, postpone

    # A station ID that would follow a break or another spoken item, or land next to a
    # break, moves one song later (or is skipped) (services/station_id_guard.py); at most a few in a row
    for _ in range(4):
        result = await db.execute(
            select(QueueEntry)
            .options(joinedload(QueueEntry.asset))
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now_utc),
            )
            .order_by(QueueEntry.position)
            .limit(1)
        )
        entry = result.unique().scalar_one_or_none()
        if entry is None or not await must_wait(db, station_id, entry, now_utc):
            return entry
        if not await postpone(db, station_id, entry):  # one song later, or skipped
            entry.status = "skipped"
            await db.flush()
    return None


# Items this short (station IDs, time checks) hand over at their end, not with a crossfade
SHORT_ITEM_SECONDS = 20.0
SHORT_ITEM_HANDOVER_SECONDS = 0.3


# Only music is crossfaded. Anything spoken (sponsorship, shiur, story, weather, jingle)
# plays to its last word and the next item starts after it: two sponsorships played
# back to back overlapped by 3 s (Shmuel, 2026-11-17).
CROSSFADE_TYPES = ("music",)
# Shmuel, 2026-11-17: 1 second unless a station sets its own
DEFAULT_CROSSFADE_SECONDS = 1.0
# The audio analysis stores a song's crossfade point this long before its audible end
ANALYSIS_CROSS_SECONDS = 3.0
MAX_CROSSFADE_SECONDS = 10.0
# Station setting automation_config["crossfade_seconds"] (0 = songs do not overlap),
# kept here per station id so the one hand-over rule needs no database read. The
# scheduler refreshes it every pass and the station API on save.
_station_crossfade: dict[str, float] = {}


def crossfade_setting(config: dict | None) -> float:
    value = (config or {}).get("crossfade_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_CROSSFADE_SECONDS
    return min(MAX_CROSSFADE_SECONDS, max(0.0, float(value)))


def note_station_crossfade(station) -> None:
    if station is not None:
        _station_crossfade[str(station.id)] = crossfade_setting(station.automation_config)


def is_crossfaded(asset) -> bool:
    return asset is not None and (getattr(asset, "asset_type", None) or "music").lower() in CROSSFADE_TYPES


# Shmuel, 2026-11-17 (9:39 PM ET): "Song ends first, then speech". A song followed by
# anything spoken (sponsorship, announcement, weather, jingle, shiur) is not crossfaded
# into it: it fades out to its end and the spoken item starts after it. On Main the
# last second of a song played under the first words of a sponsorship.
# Which item follows is only known from the queue, so predict_next_entry() notes it
# here per entry on air and the one hand-over rule below reads it without a database
# read. Not noted yet = a song follows (the station's crossfade).
_speech_follows: dict[str, bool] = {}
# The song's last moments fade out over at least this long before the spoken item
MIN_END_FADE_SECONDS = 0.3


def is_song(entry) -> bool:
    """A song that crossfades at its end: music, not short, not a timed or block item."""
    asset = entry.asset if entry is not None else None
    duration = (asset.duration if asset else None) or DEFAULT_DURATION
    return not (
        not is_crossfaded(asset)
        or duration < SHORT_ITEM_SECONDS
        or getattr(entry, "preempt_at", None) is not None
        or getattr(entry, "source", None) in ("ad_slot", "block")
    )


def note_speech_follows(entry, next_entry) -> bool:
    """Record whether the item after `entry` is spoken. Returns True when that changed."""
    key = str(getattr(entry, "id", "") or "")
    if not key:
        return False
    value = bool(next_entry is not None and is_song(entry) and not is_song(next_entry))
    if len(_speech_follows) > 2000:
        _speech_follows.clear()
    changed = _speech_follows.get(key, False) != value
    _speech_follows[key] = value
    return changed


def speech_follows(entry) -> bool:
    return _speech_follows.get(str(getattr(entry, "id", "") or ""), False)


def overlap_seconds(entry, speech_next: bool | None = None) -> float:
    """How long the item on air overlaps the one after it when it ends by itself:
    the station's song crossfade for a song followed by a song, 0 for everything else
    (anything spoken, and a song followed by something spoken). `speech_next` None =
    what predict_next_entry() last noted for this entry."""
    if not is_song(entry):
        return 0.0
    if speech_follows(entry) if speech_next is None else speech_next:
        return 0.0
    return _station_crossfade.get(str(getattr(entry, "station_id", "")), DEFAULT_CROSSFADE_SECONDS)


def end_fade_seconds(entry) -> float:
    """How long a song fades out at its end when a spoken item follows it (the spoken
    item starts once the fade is done); 0 for everything else."""
    if not (is_song(entry) and speech_follows(entry)):
        return 0.0
    crossfade = _station_crossfade.get(str(getattr(entry, "station_id", "")), DEFAULT_CROSSFADE_SECONDS)
    return max(MIN_END_FADE_SECONDS, crossfade)


def handover_seconds(entry) -> float:
    """How long before its end the item on air hands over to the next one. The one rule
    for the scheduler's timer, the now-playing message (cross_start) and the queue's
    look-ahead, so the server and the listeners' pages change at the same moment."""
    asset = entry.asset if entry is not None else None
    duration = (asset.duration if asset else None) or DEFAULT_DURATION
    extra = getattr(asset, "metadata_extra", None) if asset else None
    analysis = stored_analysis(extra) if extra else {}
    cross_start = analysis.get("cross_start_seconds")
    overlap = overlap_seconds(entry)
    if not is_song(entry):
        # A time check, weather or sponsorship is not crossfaded: handing over at
        # half its length cut a 5 s time check after 2.5 s and the shiur it had
        # paused started over it (Main, 15:00 ET 2026-11-16; All Day Stories,
        # 22:00 ET: 1.8 s of the time check under the story).
        cross_seconds = SHORT_ITEM_HANDOVER_SECONDS
    elif isinstance(cross_start, (int, float)) and 0 < cross_start < duration:
        # The analysis puts the crossfade point 3 s before the song's audible end;
        # a shorter or longer station crossfade moves the hand-over with it.
        audible_end = min(duration, cross_start + ANALYSIS_CROSS_SECONDS)
        cross_seconds = duration - audible_end + max(overlap, SHORT_ITEM_HANDOVER_SECONDS)
    else:
        cross_seconds = max(overlap, SHORT_ITEM_HANDOVER_SECONDS)
    return min(cross_seconds, max(0.0, duration / 2))


def switch_time(entry) -> datetime | None:
    """When the item on air hands over to the next one: where listeners start
    their crossfade (cross_start, default 3 s before the end). The scheduler
    advances the queue at this moment (SchedulerEngine._advance_queue)."""
    if not entry or not entry.started_at:
        return None
    asset = entry.asset
    duration = (asset.duration if asset else None) or DEFAULT_DURATION
    return _aware(entry.started_at) + timedelta(seconds=duration - handover_seconds(entry))


async def predict_next_entry(db: AsyncSession, station_id, playing_entry) -> QueueEntry | None:
    """What will follow the item on air when it ends: the pick the queue will make
    at the hand-over (a soft sponsorship due by then wins over the next song).
    Listeners pre-load this, so it must match the server's choice exactly;
    picking by 'now' sent them the next song while a sponsorship was waiting."""
    from sqlalchemy.orm import joinedload

    now_utc = datetime.now(timezone.utc)
    entry = None
    # A song hands over later when a spoken item follows it (it is not crossfaded into
    # it), which can bring one more item due: at most one second look.
    for _ in range(2):
        at = switch_time(playing_entry) or now_utc
        picked = await _pick_next_pending(db, station_id, max(at, now_utc))
        entry = None
        if picked is not None:
            entry = (await db.execute(
                select(QueueEntry).options(joinedload(QueueEntry.asset)).where(QueueEntry.id == picked.id)
            )).unique().scalar_one_or_none()
        if not note_speech_follows(playing_entry, entry):
            break
    return entry


async def _refill_for_next(db: AsyncSession, station_id) -> QueueEntry | None:
    """Nothing is left to play: fill the queue (schedule, rotation, rules) and
    return what should go on air now. A station must never stop just because
    its queue ran out, so if the normal fill finds nothing, any music is queued,
    and failing that any playable item."""
    try:
        await _replenish_queue(db, station_id)
    except Exception as exc:
        logger.warning("Queue refill failed for station %s: %s", station_id, exc)
    now_utc = datetime.now(timezone.utc)
    entry = await _pick_next_pending(db, station_id, now_utc)
    if entry:
        return entry

    for only_music in (True, False):
        from app.services.station_blocks import blocked_asset_ids

        query = select(Asset).where(
            Asset.file_path.isnot(None), Asset.file_path != "", fit_for_random_pick(),
            Asset.id.notin_(blocked_asset_ids(station_id)),
        )
        if only_music:
            query = query.where(Asset.asset_type == "music")
        else:
            query = query.where(Asset.asset_type.notin_(_NOT_FILLER_TYPES))
        assets = (await db.execute(query.order_by(func.random()).limit(20))).scalars().all()
        if assets:
            break
    if not assets:
        logger.error("Station %s has nothing to play: the library has no playable items", station_id)
        return None
    top = (await db.execute(
        select(func.coalesce(func.max(QueueEntry.position), 0))
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
    )).scalar() or 0
    for i, asset in enumerate(assets, start=1):
        db.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id,
            position=top + i, status="pending", source="fallback",
        ))
    await db.flush()
    logger.warning("Station %s ran dry; queued %d fallback items", station_id, len(assets))
    return await _pick_next_pending(db, station_id, now_utc)


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


def _is_timed_entry(entry) -> bool:
    """Time check, weather, sponsorship or program block: items that air at a set time."""
    return entry.preempt_at is not None or entry.source in ("ad_slot", "block")


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


_operator_locks: dict[str, asyncio.Lock] = {}
# Longest an operator's action waits for the one before it (a Next that waits for the
# engine to load a file holds its turn about 3 s). After that it goes ahead anyway.
OPERATOR_TURN_WAIT = 8.0


async def operator_turn(station_id: uuid.UUID):
    """One operator action at a time per station, in the order they arrived.

    Play next and Next sent 0.1 s apart ran side by side: Next read the queue before
    Play next's commit, saw the old head and played the song the engine had pre-loaded
    (Jerusalem 03:23Z 2026-11-18). A route dependency, so the turn is taken before the
    handler reads anything. Not the advance lock: the handlers take that themselves.
    """
    key = str(station_id)
    lock = _operator_locks.get(key)
    if lock is None:
        lock = _operator_locks[key] = asyncio.Lock()
    held = False
    try:
        await asyncio.wait_for(lock.acquire(), OPERATOR_TURN_WAIT)
        held = True
    except TimeoutError:
        logger.warning("Operator action on %s went ahead without its turn (waited %.0f s)", key, OPERATOR_TURN_WAIT)
    try:
        yield
    finally:
        if held:
            lock.release()


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
    from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

    async with advance_lock(station_id):
        # Another request (a refill, a schedule change, an operator's delete) can
        # remove the item this advance is starting between reading and committing
        # it. Start over from the committed state instead of giving up: giving up
        # left the station idle until someone pressed Next.
        for attempt in range(3):
            try:
                return await _check_advance_locked(db, station_id, early_seconds)
            except (StaleDataError, ObjectDeletedError) as exc:
                await db.rollback()
                logger.warning(
                    "Advance for station %s lost a row to another change (%s); retrying",
                    station_id, exc,
                )
        return await _check_advance_locked(db, station_id, early_seconds)


async def _drop_timed_twins(db: AsyncSession, entry: QueueEntry) -> None:
    """A timed item that starts takes any pending copy of itself (same item, same
    time) out of the queue. Two refills racing queued the 1 PM time check twice on
    Main (2026-11-16) and both played, the second over the start of the next one."""
    if entry is None or entry.preempt_at is None:
        return
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == entry.station_id,
            QueueEntry.status == "pending",
            QueueEntry.asset_id == entry.asset_id,
            QueueEntry.preempt_at == entry.preempt_at,
            QueueEntry.id != entry.id,
        )
        .values(status="skipped")
        .execution_options(synchronize_session=False)
    )


def _same_timed_group(current: QueueEntry, entry: QueueEntry) -> bool:
    return (
        current.preempt_at is not None
        and entry.preempt_at is not None
        and _aware(current.preempt_at) == _aware(entry.preempt_at)
    )


async def _long_item_policy(db: AsyncSession, station_id, current: QueueEntry) -> str | None:
    """What a hard timed item does to the item on air: None for music (it is cut),
    else the station's choice for a shiur or story ("pause", "wait" or "cut")."""
    from app.models.station import Station
    from app.services import long_items

    if not long_items.is_long_form(current.asset):
        return None
    station = await db.get(Station, station_id)
    return long_items.policy(station)


async def _defer_hard_until_end(db: AsyncSession, station_id, current: QueueEntry) -> int:
    """Hard timed items due before the shiur on air ends become soft: they play
    straight after it, and no player fades it for them. Returns how many changed."""
    from app.services import long_items

    end = long_items.ends_at(current)
    if end is None:
        return 0
    res = await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= end,
            or_(hard_timed(), QueueEntry.hard_by.isnot(None)),
        )
        .values(timing_mode="soft", hard_by=None)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount or 0


# How far ahead a soft item that will pause a shiur is made hard on the browser player:
# beyond the control room's 60 s pre-load, plus a queue refresh
SOFT_PAUSE_LOOKAHEAD_SECONDS = 90.0


async def _sponsorship_only_groups(db: AsyncSession, station_id, current: QueueEntry, end: datetime) -> list:
    """The preempt_at of each pending timed group, due before the long item on air
    ends, whose entries are all sponsorships (clock-slot plays, or contract audio)."""
    from app.models.spot_schedule import SpotSchedule

    rows = (
        await db.execute(
            select(QueueEntry.preempt_at, QueueEntry.source, Asset.id, Asset.sponsor_id)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at < end,
            )
        )
    ).all()
    if not rows:
        return []
    contract = set(
        (await db.execute(
            select(SpotSchedule.asset_id).where(SpotSchedule.asset_id.in_({r[2] for r in rows}))
        )).scalars().all()
    )
    groups: dict = {}
    for at, source, asset_id, sponsor_id in rows:
        if current.preempt_at is not None and _aware(current.preempt_at) == _aware(at):
            continue
        sponsorship = source == "ad_slot" or asset_id in contract or sponsor_id is not None
        groups[at] = groups.get(at, True) and sponsorship
    return [at for at, only in groups.items() if only]


async def _soft_items_pause_long(
    db: AsyncSession,
    station_id,
    current: QueueEntry,
    policy: str | None,
    now_utc: datetime,
    horizon: datetime | None = None,
    include_due: bool = True,
) -> int:
    """Soft timed items due while a shiur or story has more than five minutes left
    become hard, so the station's rule (pause by default) lets them in; with less
    left they keep waiting for its end (Shmuel, 2026-11-17: the 7:30 weather aired at
    7:49 behind a shiur). Items due up to `horizon` are looked at (default: now), so
    they turn hard ahead of time: the control room then pre-loads them and fades the
    shiur exactly on time, and the scheduler arms its exact timer. Done only when due,
    the weather started on the next 3 s poll (+1.7 s live) without a pre-load. The
    engine passes include_due=False (an overdue item cannot go to its timed lane).
    Nothing changes while HS is off. Returns how many entries were made hard."""
    from app.models.queue_entry import soft_timed
    from app.models.station import Station
    from app.services import long_items
    from app.services.station_modes import hard_start_on

    if policy not in ("pause", "cut") or current is None:
        return 0
    # HS off: the operator wants nothing to cut in, so soft items keep waiting
    if not hard_start_on(await db.get(Station, station_id)):
        return 0
    end = long_items.ends_at(current)
    if end is None:
        return 0
    # On a station that says so (long_items.sponsorships_wait), a group of sponsorships
    # alone never cuts into a shiur or story: it waits for its end and plays between
    # stories (coordinator 2026-11-19 for All Day Stories: hard :15/:30/:45
    # sponsorships cut into stories 25 times a day in the simulator).
    # Sponsorships that ride with a time check or weather still play in that pause.
    station = await db.get(Station, station_id)
    sponsor_only = (
        await _sponsorship_only_groups(db, station_id, current, end)
        if long_items.sponsorships_wait(station) else []
    )
    if sponsor_only:
        await db.execute(
            update(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.in_(sponsor_only),
            )
            .values(timing_mode="soft", hard_by=None)
            .execution_options(synchronize_session=False)
        )
    rows = (
        await db.execute(
            select(QueueEntry.preempt_at)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                soft_timed(),
                QueueEntry.preempt_at <= (horizon or now_utc),
                *([] if include_due else [QueueEntry.preempt_at > now_utc]),
            )
            .distinct()
        )
    ).scalars().all()
    changed = 0
    for at in rows:
        at_aware = _aware(at)
        if current.preempt_at is not None and _aware(current.preempt_at) == at_aware:
            continue  # the shiur is itself part of this group
        if at in sponsor_only:
            continue  # sponsorships alone wait for the end
        due = max(at_aware, now_utc)
        if (end - due).total_seconds() <= long_items.SOFT_PAUSE_MIN_LEFT_SECONDS:
            continue
        res = await db.execute(
            update(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at == at,
                soft_timed(),
            )
            .values(timing_mode="hard", hard_by=None, was_soft=True)
            .execution_options(synchronize_session=False)
        )
        changed += res.rowcount or 0
        logger.info(
            "Station %s: soft group at %s pauses %s (%.0f s left)",
            station_id, at_aware.isoformat(), current.asset_id, (end - due).total_seconds(),
        )
    return changed


async def _queue_resume(
    db: AsyncSession, station_id, current: QueueEntry, timed: QueueEntry, now_utc: datetime
) -> None:
    """Queue the shiur on air again, straight after the timed group that pauses it,
    to carry on from where it stops."""
    from app.models.queue_entry import resume_source
    from app.services import long_items

    at = long_items.resume_point(current, now_utc)
    if at is None:
        return
    group_end = (
        await db.execute(
            select(func.max(QueueEntry.position)).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at == timed.preempt_at,
            )
        )
    ).scalar() or timed.position
    # First after the group, ahead of every other pending item: a song queued at a
    # lower position than the timed item would otherwise play before the shiur
    # carries on (the group's own items are due and hard, so they still go first)
    first_other = (
        await db.execute(
            select(func.min(QueueEntry.position)).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at != timed.preempt_at),
            )
        )
    ).scalar()
    position = group_end + 1 if first_other is None else min(group_end + 1, first_other)
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position >= position,
        )
        .values(position=QueueEntry.position + 1)
        .execution_options(synchronize_session=False)
    )
    db.add(QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
        position=position, status="pending", source=resume_source(at),
    ))
    logger.info(
        "Station %s: paused %s at %.0f s for timed entry %s; it resumes after",
        station_id, current.asset_id, at, timed.id,
    )


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
        mark_started(current, datetime.now(timezone.utc))
        await db.commit()
        return current

    # Check if a pending entry needs to preempt the current track (exact-time playback)
    now_utc = datetime.now(timezone.utc)
    long_policy = await _long_item_policy(db, station_id, current)
    if long_policy == "wait":
        # The station lets a shiur finish: timed items due before its end wait for it.
        # Saved at once, or pages would still see them hard and cut the shiur locally.
        if await _defer_hard_until_end(db, station_id, current):
            await db.commit()
    elif long_policy:
        if await _soft_items_pause_long(
            db, station_id, current, long_policy, now_utc,
            horizon=now_utc + timedelta(seconds=SOFT_PAUSE_LOOKAHEAD_SECONDS),
        ):
            # Save it now: the poll rolls back after a check that changes nothing on
            # air, and pages must see the item turn hard ahead to pre-load it
            await db.commit()
    # A hard group due while an earlier short group is still playing follows it instead
    # of cutting it (V1-10b). Done ahead, so pages pre-load the new time.
    from app.services.timed_groups import space_hard_groups

    if await space_hard_groups(
        db, station_id, now_utc, now_utc + timedelta(seconds=SOFT_PAUSE_LOOKAHEAD_SECONDS)
    ):
        await db.commit()
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
    if (
        preempt_entry
        and is_blackout
        and (preempt_entry.asset is None or preempt_entry.asset.asset_type != "silence")
    ):
        # Shabbos / Yom Tov: nothing but silence goes on air, timed or not
        preempt_entry = None
    if preempt_entry and _same_timed_group(current, preempt_entry):
        # The item on air belongs to the same timed group (time check, then weather):
        # the rest of the group follows it, it does not cut it
        preempt_entry = None
    if preempt_entry:
        # Don't preempt silence with more silence — let it finish naturally
        if current.asset_id != preempt_entry.asset_id:
            if long_policy == "pause":
                await _queue_resume(db, station_id, current, preempt_entry, now_utc)
            # Stop current track and start the preempt entry immediately
            if current.started_at:
                log = PlayLog(
                    id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                    start_utc=aired_from(current), end_utc=now_utc, source="scheduler",
                )
                db.add(log)
            current.status = "played"
            mark_started(preempt_entry, now_utc)
            await _drop_timed_twins(db, preempt_entry)
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
                start_utc=aired_from(current), end_utc=now_utc, source="scheduler",
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
            start_utc=aired_from(current),
            end_utc=datetime.now(timezone.utc),
            source="scheduler",
        )
        db.add(log)
        current.status = "played"
        # Looping: the same item goes in again as the next thing to play
        await add_loop_repeat(db, station_id)

    if is_blackout:
        next_entry = await _pick_next_pending(db, station_id, now_utc, silence_only=True)
    else:
        next_entry = await _pick_next_pending(db, station_id, now_utc)
    if not next_entry and not is_blackout:
        # The queue ran out: fill it and carry on rather than going silent
        next_entry = await _refill_for_next(db, station_id)
    if next_entry:
        mark_started(next_entry, datetime.now(timezone.utc))
        await _drop_timed_twins(db, next_entry)
        if current is not None:
            _note_automatic_change(station_id, next_entry.id)
        logger.info(
            "Station %s: started entry %s (asset %s, source %s) after %s",
            station_id, next_entry.id, next_entry.asset_id, next_entry.source,
            current.id if current is not None else "nothing",
        )

        # Compact positions: ensure playing entry is at position 1
        # and pending entries follow sequentially (prevents stale position drift)
        # (one statement: row by row it was hundreds of UPDATEs on a two-day queue,
        # all before the new item was saved and announced)
        from app.services.queue_positions import compact_positions

        await compact_positions(db, station_id)
        await db.commit()
        # Top the queue up afterwards, not here: the caller announces the new item
        # first. A refill that took long (one did at the top of an hour) used to
        # hold the announcement, so listeners heard the next song from their own
        # pre-load and then the item that had really started, minutes late.
        if not is_blackout:
            _replenish_soon(station_id, rebroadcast=True)
        return next_entry

    await db.commit()
    # The item on air ended and nothing followed it (a blackout with no silence
    # queued, or a refill that found nothing): tell open pages
    await _broadcast_nothing_playing(station_id)
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
        # Never answer a failed read with an empty queue: the control room would
        # show the station as stopped with nothing queued while it is playing.
        return JSONResponse(status_code=503, content={"detail": "The queue could not be read. Try again."})


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return jsonable_encoder(value)


def _next_due(entries, now_utc: datetime):
    """In-memory twin of _pick_next_pending for rows already loaded."""
    pending = [e for e in entries if e.status == "pending"]
    timed_due = [e for e in pending if e.preempt_at and _aware(e.preempt_at) <= now_utc]
    if timed_due:
        return min(timed_due, key=lambda e: (_aware(e.preempt_at), e.position))
    soon = [
        e for e in pending
        if e.preempt_at
        and now_utc < _aware(e.preempt_at) <= now_utc + timedelta(seconds=TIMED_EARLY_START_SECONDS)
    ]
    if soon:
        return min(soon, key=lambda e: (_aware(e.preempt_at), e.position))
    regular = [e for e in pending if not e.preempt_at or _aware(e.preempt_at) <= now_utc]
    return min(regular, key=lambda e: e.position) if regular else None


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
        QueueEntry.block_id,
    )
    asset_cols = (
        Asset.id, Asset.title, Asset.artist, Asset.duration, Asset.intro_seconds,
        Asset.asset_type, Asset.category, Asset.sponsor_id,
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
    # What Next will actually play now (the same rule as _pick_next_pending), and
    # what really follows the item on air when it ends. They differ when a timed
    # item comes due before the hand-over: the :30 weather was due by the end of
    # the song, but "Up next" showed the song after it (Shmuel, 2026-11-16).
    now_q = datetime.now(timezone.utc)
    skip_entry = _next_due(entries, now_q)
    hand_over = switch_time(now_playing_entry) if now_playing_entry else None
    next_entry = _next_due(entries, max(hand_over, now_q)) if hand_over else skip_entry

    # Sort entries by simulating actual playback order:
    # - Regular songs play sequentially by position
    # - Hard preempts (hourly time/weather) interrupt at their exact preempt_at
    # - Soft preempts (ad_slots) play after current song finishes when their time arrives
    # Each item holds the air up to where the next one crossfades in (on_air_seconds),
    # as the players and the fill count it; full lengths put everything about 3 s
    # later per song, and an hour on the station IDs showed up beside breaks the
    # fill had kept them clear of (2026-11-19)
    from app.services.playlist_generator_service import on_air_seconds as _on_air

    def _air(asset) -> float:
        if asset is None:
            return float(DEFAULT_DURATION)
        if getattr(asset, "asset_type", None) == "silence":
            # a blackout's silences fill its window end to end (311 x 5 min for a
            # Shabbos); 3 s off each put Motzei Shabbos 15 min early
            return float(getattr(asset, "duration", None) or DEFAULT_DURATION)
        meta = getattr(asset, "metadata_extra", None)
        meta = meta if isinstance(meta, dict) else None
        return _on_air(getattr(asset, "duration", None) or DEFAULT_DURATION, meta)

    try:
        now_utc_sort = datetime.now(timezone.utc)
        _np_sa = now_playing_entry.started_at if now_playing_entry and now_playing_entry.started_at else None
        if _np_sa and _np_sa.tzinfo is None:
            _np_sa = _np_sa.replace(tzinfo=timezone.utc)

        if _np_sa:
            _ad = _air(now_playing_entry.asset)
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
        _timed_end = now_utc_sort  # end of the last timed item placed
        _safety = len(entries) * 3  # prevent infinite loops

        # A soft item due while a shiur or story has more than 5 minutes left pauses
        # it (long_items, Shmuel 2026-11-17). The estimate left the sponsorship waiting
        # behind the whole shiur: an 8:30 PM slot was shown at 9:00:45 behind the Daf Yomi.
        from app.models.station import Station as _Station
        from app.services import long_items as _long
        from app.services.station_modes import hard_start_on as _hs_on

        _st = await db.get(_Station, station_id)
        _long_policy = _long.policy(_st) if _st is not None and _hs_on(_st) else "wait"

        def _soft_into_long(asset, start, end):
            """Place the soft items that will pause the long item playing start..end;
            returns when the air is free again."""
            nonlocal si
            if _long_policy not in ("pause", "cut") or not _long.is_long_form(asset):
                return end
            while si < len(soft_preempts):
                due = max(_tz(soft_preempts[si].preempt_at), start)
                if due >= end or (end - due).total_seconds() <= _long.SOFT_PAUSE_MIN_LEFT_SECONDS:
                    break
                _d = _air(soft_preempts[si].asset)
                _est_map[soft_preempts[si].id] = due
                # paused: it carries on afterwards; cut: the air is free after the item
                end = end + timedelta(seconds=_d) if _long_policy == "pause" else due + timedelta(seconds=_d)
                si += 1
                if _long_policy == "cut":
                    break
            return end

        if now_playing_entry is not None and _np_sa:
            _cursor = _soft_into_long(now_playing_entry.asset, now_utc_sort, _cursor)

        while (ri < len(regulars) or hi < len(hard_preempts) or si < len(soft_preempts)) and _safety > 0:
            _safety -= 1
            # 1. Fire hard preempt if its time has arrived
            if hi < len(hard_preempts):
                hp_pa = _tz(hard_preempts[hi].preempt_at)
                if hp_pa <= _cursor:
                    # A hard item cuts the song under it, but never another timed item
                    # or the past: a group sharing one time (a blackout's silence, all
                    # due at its start) plays one after another. Jumping back to hp_pa
                    # gave all 311 of KBR Jerusalem's Shabbos silences the same start,
                    # and everything after them showed Friday times.
                    _cursor = max(hp_pa, _timed_end)
                    _est_map[hard_preempts[hi].id] = _cursor
                    _dur = _air(hard_preempts[hi].asset)
                    _cursor = _soft_into_long(
                        hard_preempts[hi].asset, _cursor, _cursor + timedelta(seconds=_dur or DEFAULT_DURATION)
                    )
                    _timed_end = _cursor
                    hi += 1
                    continue

            # 2. Fire soft preempt (ad_slot) if its time has arrived
            if si < len(soft_preempts):
                sp_pa = _tz(soft_preempts[si].preempt_at)
                if sp_pa <= _cursor:
                    _est_map[soft_preempts[si].id] = _cursor
                    _dur = _air(soft_preempts[si].asset)
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    _timed_end = _cursor
                    si += 1
                    continue

            # 3. Play next regular song
            if ri < len(regulars):
                song = regulars[ri]
                song_dur = _air(song.asset)
                song_dur = max(1.0, song_dur - resume_offset(song))  # a paused shiur's remainder
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
                _cursor = _soft_into_long(song.asset, _cursor, _cursor + timedelta(seconds=song_dur))
                ri += 1
            else:
                # No regular songs left — process remaining preempts by time
                next_hp = _tz(hard_preempts[hi].preempt_at) if hi < len(hard_preempts) else None
                next_sp = _tz(soft_preempts[si].preempt_at) if si < len(soft_preempts) else None
                if next_hp and (not next_sp or next_hp <= next_sp):
                    _cursor = max(_cursor, next_hp)
                    _est_map[hard_preempts[hi].id] = _cursor
                    _dur = _air(hard_preempts[hi].asset)
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    _timed_end = _cursor
                    hi += 1
                elif next_sp:
                    _cursor = max(_cursor, next_sp)
                    _est_map[soft_preempts[si].id] = _cursor
                    _dur = _air(soft_preempts[si].asset)
                    _cursor += timedelta(seconds=_dur or DEFAULT_DURATION)
                    _timed_end = _cursor
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
        duration = (asset.duration if asset else None) or DEFAULT_DURATION
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
        asset_dur = (now_playing_entry.asset.duration if now_playing_entry.asset else None) or DEFAULT_DURATION
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

    # Holiday items in season (services/holiday_music.py): the queue row says which
    # holiday, so the operator sees exactly where they air
    holiday_of: dict[str, str] = {}
    try:
        from app.models.station import Station
        from app.services.holiday_music import holidays_for_assets

        station_row = await db.get(Station, station_id)
        if station_row is not None:
            holiday_of = await holidays_for_assets(db, station_row, {e.asset_id for e in entries})
    except Exception:
        logger.exception("Holiday tags failed for station %s", station_id)

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
        if e.status != "playing":
            dur = max(1.0, dur - resume_offset(e))  # a paused shiur's remainder
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
                "sponsor_id": str(e.asset.sponsor_id) if getattr(e.asset, "sponsor_id", None) else None,
            } if e.asset else None,
        }
        if is_silence and current_blackout_name:
            d["blackout_name"] = current_blackout_name
        if str(e.asset_id) in holiday_of:
            d["holiday"] = holiday_of[str(e.asset_id)]
        # The schedule block an automatic song was drawn for: the control room's
        # "Playing from" column names its playlist and opens why the song is there
        if getattr(e, "block_id", None):
            d["block_id"] = e.block_id
        entries_data.append(d)

    return {
        "entries": entries_data,
        "total": len(entries_data),
        "total_all": total_queue_entries,
        "now_playing": np_data,
        "next_entry_id": str(next_entry.id) if next_entry else None,
        # What the Next button plays right now (timed items not yet due are skipped)
        "skip_entry_id": str(skip_entry.id) if skip_entry else None,
        "queue_duration_seconds": round(queue_duration, 1),
        "total_queue_duration_seconds": round(total_queue_duration, 1),
        "preempt_fade_ms": 2000,
        # Set while the operator has Loop on: this asset repeats until Loop off or Next
        "loop_asset_id": await loop_asset_id(db, station_id),
        # HS and Spots mode switches (control room), see wiki/station-modes.md
        "modes": await _station_modes(db, station_id),
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
    from app.services.last_played import last_played

    rows = await last_played(db, station_id)
    return {"last_played": {asset_id: ts.isoformat() for asset_id, ts in rows.items()}}


def _station_entry(station_id, entry_id):
    """A queue entry of THIS station. Looking entries up by id alone let anyone allowed
    on one station move or delete another station's entries (the protected FM feed
    included) by sending their ids to their own station's URL."""
    return select(QueueEntry).where(QueueEntry.id == entry_id, QueueEntry.station_id == station_id)


async def _require_assets(db: AsyncSession, asset_ids) -> None:
    """404 for an unknown asset, instead of a database error (500) on insert."""
    wanted = set(asset_ids)
    found = set((await db.execute(select(Asset.id).where(Asset.id.in_(wanted)))).scalars().all())
    if wanted - found:
        raise NotFoundError("Asset not found")


@router.post("/add", status_code=201)
async def add_to_queue(
    station_id: uuid.UUID,
    body: QueueAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    await _require_assets(db, [body.asset_id])
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
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
    await _require_assets(db, body.asset_ids)
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
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


async def _engine_takes_queue_change(db: AsyncSession, station_id) -> None:
    """After an operator changed what is next: an engine station drops the song it had
    pre-loaded if that is no longer the head, so the change is what airs next."""
    if not settings.engine_drives(station_id):
        return
    try:
        from app.services import playout_engine
        await playout_engine.take_operator_change(db, station_id)
    except Exception:
        logger.warning("Engine could not be told about the queue change", exc_info=True)


@router.post("/play-next", status_code=201, dependencies=[Depends(operator_turn)])
async def play_next(
    station_id: uuid.UUID,
    body: QueueAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    await _require_assets(db, [body.asset_id])
    # Ahead of every waiting entry, also when the one on air sits further down the
    # numbering (an engine start report can leave it there): an ID once aired ahead
    # of an operator's Play next on Jerusalem (2026-11-19)
    first_waiting = (await db.execute(
        select(func.min(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )).scalar()
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
    if first_waiting is not None:
        next_pos = min(next_pos, first_waiting)
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=body.asset_id,
        position=next_pos, status="pending", source="manual",
    )
    db.add(entry)
    await db.commit()
    await _engine_takes_queue_change(db, station_id)
    return {"id": str(entry.id), "position": entry.position, "message": "Queued as next"}


@router.post("/fire-now", status_code=201, dependencies=[Depends(operator_turn)])
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
        if uri and await playout_engine.engine_timed(station_id, uri, time.time(), str(entry.id)):
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
        # Play-now wins over a time check on air too (only Next is refused there);
        # it answered "on air" while the item was only queued behind the time check
        skipped = await _skip_current_locked(
            station_id, db, keep_incoming=False, protect_timed=False
        )
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
        if not await playout_engine.engine_overlay(station_id, asset):
            raise HTTPException(status_code=503, detail="The playout engine did not respond")
    else:
        # Browser playback: every open player mixes it in on top of its own audio
        from app.api.v1.websocket import manager as ws_manager

        analysis = stored_analysis(asset.metadata_extra)
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


@router.post("/skip", dependencies=[Depends(operator_turn)])
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
        on_air = (await db.execute(
            select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
        )).scalar_one_or_none()
        if on_air is not None and _is_timed_entry(on_air):
            return {"message": "Timed item plays to the end", "now_playing": str(on_air.asset_id),
                    "kept_incoming": True, "protected": True, "fade_seconds": fade_seconds}
        # Next ends a loop: the pre-loaded repeat must not come back
        if await stop_loop(db, station_id):
            await db.commit()
            await playout_engine.engine_flush(station_id)
        else:
            # The engine may hold a song the queue no longer has next (Play next, a delete
            # or a move seconds ago, a sponsorship that became due): Next must play what
            # the queue shows. That item is loaded first and then put on air; taking the
            # song back and skipping at once lost the item while its file was loading.
            state = playout_engine.get_state(station_id)
            head = None
            if on_air is None or str(on_air.id) not in state.timed_sent:
                head = await playout_engine.head_the_engine_does_not_hold(db, station_id)
            if head is not None and await playout_engine.next_goes_to(station_id, head):
                from app.services import activity_log
                activity_log.note("Sent to the playout engine", [on_air.asset_id if on_air else None])
                return {"message": "Skipped", "now_playing": None, "fade_seconds": fade_seconds}
            # A long item, or a hard item close by: the pre-loaded song is taken back
            # and the engine asks again...
            await playout_engine.take_operator_change(db, station_id, before_next=True)
            # ...and needs a moment to load the item it was handed just now
            await playout_engine.settle_before_next(station_id)
        if await playout_engine.engine_skip(station_id, fade_seconds):
            from app.services import activity_log
            activity_log.note("Sent to the playout engine", [on_air.asset_id if on_air else None])
            return {"message": "Skipped", "now_playing": None, "fade_seconds": fade_seconds}
        return JSONResponse(
            status_code=503,
            content={"detail": "The playout engine did not respond. Nothing was skipped."},
        )

    # Same lock as the automatic advance, so Next cannot collide with a song change
    async with advance_lock(station_id):
        done = await _skip_current_locked(station_id, db, fade_seconds=fade_seconds)
    if done.get("message") != "Skipped":
        # Nothing changed hands: say why on the activity page
        from app.services import activity_log
        activity_log.note(done.get("message") or "Nothing skipped", [done.get("now_playing")])
    return {**done, "fade_seconds": fade_seconds}


async def _station_modes(db: AsyncSession, station_id) -> dict:
    from app.models.station import Station
    from app.services.station_modes import modes

    return modes(await db.get(Station, station_id))


async def _skip_current_locked(
    station_id: uuid.UUID,
    db: AsyncSession,
    fade_seconds: float | None = None,
    keep_incoming: bool = True,
    protect_timed: bool = True,
    force: bool = False,
):
    """`protect_timed`: Next leaves a timed item on air alone; play-now (False) does not.
    `force` (Panic) cuts even a protected timed item."""
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
        .execution_options(populate_existing=True)
    )
    playing_entries = result.scalars().all()
    current = playing_entries[0] if playing_entries else None
    if current and protect_timed and not force and _is_timed_entry(current):
        # Next never skips a time check, weather or sponsorship: it plays to the end and
        # Next works again after it (Shmuel, 2026-11-16: a burst of Nexts skipped the
        # Jerusalem weather 3.2 s in). Play-now still puts something else on air.
        return {
            "message": "Timed item plays to the end",
            "now_playing": str(current.asset_id),
            "kept_incoming": True,
            "protected": True,
            "queue": await _queue_after_action(db, station_id),
        }
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
                start_utc=aired_from(current), end_utc=datetime.now(timezone.utc),
                source="manual",
            )
            db.add(log)
        current.status = "skipped"

    # Advance to next — same logic as _check_advance:
    # 1. Check for soft-preempt ad slots whose time has arrived
    # 2. Then find next regular entry (skip future preempts)
    now_skip = datetime.now(timezone.utc)
    # During a Shabbos / Yom Tov blackout, Next and Panic may only start the blackout's
    # silence, never a queued song (the scheduler's own advance follows the same rule).
    blacked_out = await _is_blacked_out(db, station_id)
    next_entry = await _pick_next_pending(db, station_id, now_skip, silence_only=blacked_out)
    refilled = False
    if not next_entry and not blacked_out:
        # The last item was skipped: fill the queue and carry on, never stop
        next_entry = await _refill_for_next(db, station_id)
        refilled = True

    # Skip in Liquidsoap to keep it in sync
    if settings.liquidsoap_enabled:
        try:
            from app.services.liquidsoap_client import skip as ls_skip
            await ls_skip()
        except Exception:
            pass

    if next_entry:
        mark_started(next_entry, datetime.now(timezone.utc))
        await _drop_timed_twins(db, next_entry)
        next_id = next_entry.id
        next_asset_id = next_entry.asset_id
        await db.commit()
        # Listeners first: the new item goes out before anything else is done
        await _broadcast_now_playing_entry(db, station_id, next_id, fade_seconds)
        if not refilled:
            # Topping the queue up to 24 hours took most of Next's 2 s; it can wait
            _replenish_soon(station_id)
        return {
            "message": "Skipped",
            "now_playing": str(next_asset_id),
            "queue": await _queue_after_action(db, station_id),
        }

    if not refilled:
        await _replenish_queue(db, station_id)
    await db.commit()
    if current is not None:
        await _broadcast_nothing_playing(station_id)
    return {"message": "Queue empty", "now_playing": None, "queue": await _queue_after_action(db, station_id)}


_background: set[asyncio.Task] = set()


# A refill slower than this is logged, to find what makes it slow
SLOW_REFILL_SECONDS = 5.0
_refill_locks: dict[str, asyncio.Lock] = {}


def _refill_lock(station_id) -> asyncio.Lock:
    key = str(station_id)
    lock = _refill_locks.get(key)
    if lock is None:
        lock = _refill_locks[key] = asyncio.Lock()
    return lock


def _replenish_soon(station_id, rebroadcast: bool = False) -> None:
    """Top the queue up after the change, in its own session. Not under the track
    change lock: a slow refill held there would also hold the next change (a short
    time check would end into silence). Refills of one station run one at a time,
    and the refill's deletes filter on status in SQL, so an item a change has just
    started is never removed. With `rebroadcast`, the item on air is announced again
    afterwards, so what listeners pre-load as "next" includes anything the refill
    added (an hourly time check, a block). Failures are logged; the scheduler's
    next fill covers them."""
    async def run():
        from app.db.engine import async_session_factory
        started = time.monotonic()
        try:
            async with _refill_lock(station_id):
                async with async_session_factory() as session:
                    await _replenish_queue(session, station_id)
                    await session.commit()
                    took = time.monotonic() - started
                    if took > SLOW_REFILL_SECONDS:
                        logger.warning("Station %s: queue refill took %.1f s", station_id, took)
                    if rebroadcast:
                        playing = (await session.execute(
                            select(QueueEntry.id).where(
                                QueueEntry.station_id == station_id, QueueEntry.status == "playing"
                            ).order_by(QueueEntry.started_at.desc().nullslast()).limit(1)
                        )).scalar_one_or_none()
                        if playing is not None:
                            await _broadcast_now_playing_entry(session, station_id, playing)
        except Exception as exc:
            logger.warning("Queue top-up failed for station %s: %s", station_id, exc)

    task = asyncio.get_running_loop().create_task(run())
    _background.add(task)
    task.add_done_callback(_background.discard)


# Rows of the queue sent back with an action: what the control room shows and polls
# (frontend QUEUE_FIRST_ROWS). It was 2000: on a two-day queue every Next sent ten
# times what the screen had loaded (790 KB against 79 KB).
ACTION_QUEUE_ROWS = 150


async def _queue_after_action(db: AsyncSession, station_id) -> dict | None:
    """The same body as GET /queue (its first rows), returned with an action's
    response so the client does not need a second request to show the result."""
    try:
        return await _get_queue_impl(station_id, ACTION_QUEUE_ROWS, db)
    except Exception as exc:
        logger.warning("Queue snapshot after action failed: %s", exc)
        return None


async def _broadcast_nothing_playing(station_id) -> None:
    """Nothing is on air any more: open listener pages must stop showing the old item."""
    from app.api.v1.websocket import broadcast_nothing_playing

    await broadcast_nothing_playing(station_id)


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


@router.post("/modes")
async def set_station_modes(
    station_id: uuid.UUID,
    body: QueueModes,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """KBR Studio's HS and Spots mode switches. HS off: timed items wait for the song
    on air. Spots mode off: no sponsorship spots are queued. See wiki/station-modes.md."""
    from app.models.station import Station
    from app.services.station_modes import set_modes

    station = await db.get(Station, station_id)
    if station is None:
        raise NotFoundError("Station not found")
    async with advance_lock(station_id):
        result = await set_modes(db, station, body.hard_start, body.spots)
        await db.commit()
    # Every other open control room shows the new state at once (not on its next 10 s poll)
    try:
        from app.api.v1.websocket import manager as ws_manager

        await ws_manager.broadcast_to_station(str(station_id), {
            "type": "modes", "data": {"station_id": str(station_id), **result},
        })
    except Exception:
        logger.warning("Mode change broadcast failed", exc_info=True)
    if settings.engine_drives(station_id):
        # The engine may hold a timed item or a spot as its next track: make it ask again
        from app.services import playout_engine
        await playout_engine.engine_flush(station_id)
    return {"modes": result, "queue": await _queue_after_action(db, station_id)}


# A second Panic within this many seconds (another control room, a double press) is the
# same emergency: it does not cut the item the first one just started.
PANIC_REPEAT_SECONDS = 1.5
_last_panic: dict[str, float] = {}


@router.post("/panic")
async def panic(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
):
    """Panic: cut whatever is on air at once (even a time check or a looping item)
    and play the next good item; an empty queue is filled first. Logged as an alert."""
    from app.models.station import Station
    from app.services.alert_service import create_alert

    station = await db.get(Station, station_id)
    if station is not None and (station.automation_config or {}).get("live_show_id"):
        # The live show owns the air: Panic starts nothing from the queue under the host
        # (ending the show is the host's End show).
        return {"message": "A live show is on air: Panic does nothing until it ends",
                "queue": await _queue_after_action(db, station_id)}

    if settings.engine_drives(station_id):
        from app.services import playout_engine
        last = _last_panic.get(str(station_id))
        if last is not None and time.monotonic() - last < PANIC_REPEAT_SECONDS:
            return {"message": "Already cut by another Panic just now",
                    "queue": await _queue_after_action(db, station_id)}
        _last_panic[str(station_id)] = time.monotonic()
        await stop_loop(db, station_id)
        await db.commit()
        await playout_engine.engine_flush(station_id)
        ok = await playout_engine.engine_skip(station_id, SKIP_FADE_SECONDS["hard"])
        done = {"message": "Skipped" if ok else "The playout engine did not respond"}
    else:
        async with advance_lock(station_id):
            # Two control rooms pressing Panic at the same moment: the second press arrives
            # just after the first started the next item; it must not cut that one too.
            last = _last_panic.get(str(station_id))
            if last is not None and time.monotonic() - last < PANIC_REPEAT_SECONDS:
                done = {"message": "Already cut by another Panic just now"}
            else:
                done = await _skip_current_locked(
                    station_id, db, fade_seconds=SKIP_FADE_SECONDS["hard"], keep_incoming=False, force=True
                )
                _last_panic[str(station_id)] = time.monotonic()
    try:
        await create_alert(
            db, alert_type="system", severity="warning",
            title="Panic pressed in the control room",
            message=(f"{user.email} pressed Panic and cut what was on air."
                     if done.get("message") == "Skipped"
                     else f"{user.email} pressed Panic: {done.get('message')}"),
            station_id=station_id,
        )
        await db.commit()
    except Exception:
        logger.warning("Panic alert could not be written", exc_info=True)
        await db.rollback()
    return {**done, "queue": await _queue_after_action(db, station_id)}


@router.post("/insert", status_code=201, dependencies=[Depends(operator_turn)])
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


async def _warnings_where_it_stands(db: AsyncSession, station_id: uuid.UUID, entry: QueueEntry) -> list[str]:
    """The reminders for an entry at the place it was just moved to (not yet committed).
    Every way of moving an entry answers with the same ones: arrows, a typed position
    and drag-and-drop. Never blocks the move."""
    await db.flush()
    pending = list(
        (
            await db.execute(
                select(QueueEntry)
                .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
                .order_by(QueueEntry.position)
            )
        ).scalars().all()
    )
    try:
        return await _validate_queue_move(db, station_id, entry, entry.position, pending)
    except Exception:  # a reminder must never fail the move
        logger.warning("Move warnings could not be worked out", exc_info=True)
        return []


@router.post("/move-up", dependencies=[Depends(operator_turn)])
async def move_up(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Move a queue entry up (lower position number)."""
    result = await db.execute(_station_entry(station_id, body.entry_id))
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
    warnings: list[str] = []
    if above:
        above.position, entry.position = entry.position, above.position
        warnings = await _warnings_where_it_stands(db, station_id, entry)
        await db.commit()
    await _engine_takes_queue_change(db, station_id)
    return {"message": "Moved up", "warnings": warnings}


@router.post("/move-down", dependencies=[Depends(operator_turn)])
async def move_down(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Move a queue entry down (higher position number)."""
    result = await db.execute(_station_entry(station_id, body.entry_id))
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
    warnings: list[str] = []
    if below:
        below.position, entry.position = entry.position, below.position
        warnings = await _warnings_where_it_stands(db, station_id, entry)
        await db.commit()
    await _engine_takes_queue_change(db, station_id)
    return {"message": "Moved down", "warnings": warnings}


@router.put("/reorder", dependencies=[Depends(operator_turn)])
async def reorder_queue(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    # Only a waiting entry moves (like drag-and-drop): moving the item on air or a played
    # one changed nothing useful and could put it ahead of the queue (V1-42)
    result = await db.execute(
        _station_entry(station_id, body.entry_id).where(QueueEntry.status == "pending")
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    # Make room so no two waiting entries share a position: a tie left the order (and
    # which one plays next) up to the database
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.id != entry.id,
            QueueEntry.position >= body.new_position,
        )
        .values(position=QueueEntry.position + 1)
        .execution_options(synchronize_session=False)
    )
    entry.position = body.new_position
    warnings = await _warnings_where_it_stands(db, station_id, entry)
    await db.commit()
    await _engine_takes_queue_change(db, station_id)
    return {"message": "Reordered", "warnings": warnings}


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

    # This station's rules only (its own, or the global ones when it has none), at
    # the station's own clock. This used to read every station's rules at UTC hours:
    # a move on Kol B'ramah Main was warned about by the All Day Stories rule.
    from zoneinfo import ZoneInfo

    from app.models.station import Station
    from app.services.queue_replenish_service import rule_covers, rules_for_station

    tz_name = (await db.execute(select(Station.timezone).where(Station.id == station_id))).scalar()
    try:
        zone = ZoneInfo(tz_name or "UTC")
    except Exception:
        zone = timezone.utc
    local_play_time = estimated_play_time.astimezone(zone)
    daypart_rules = [
        r for r in await rules_for_station(db, station_id)
        if r.rule_type == "daypart" and rule_covers(r, local_play_time)
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


@router.post("/reorder-dnd", dependencies=[Depends(operator_turn)])
async def reorder_dnd(
    station_id: uuid.UUID,
    body: QueueDndReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Drag-and-drop reorder: move entry to new position with rule validation."""
    result = await db.execute(_station_entry(station_id, body.entry_id))
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

    await _engine_takes_queue_change(db, station_id)
    return {"message": "Reordered", "warnings": warnings}


@router.delete("/{entry_id}", status_code=204, dependencies=[Depends(operator_turn)])
async def remove_from_queue(
    station_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    result = await db.execute(_station_entry(station_id, entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    if entry.status == "playing" and not settings.engine_drives(station_id):
        # Removing what is on air moves on to the next item at once, like Next; a
        # browser station would otherwise be silent until dead-air recovery.
        async with advance_lock(station_id):
            await _remove_on_air_locked(db, station_id, entry)
        return
    await db.delete(entry)
    await db.commit()
    await _engine_takes_queue_change(db, station_id)
    # The top-up runs after the answer, in its own session: it is dozens of
    # database round trips (2.2 s on the live site for a plain delete), and the
    # deleted row stayed locked until it had finished.
    _replenish_soon(station_id)


@router.post("/{entry_id}/skip-sponsorship", dependencies=[Depends(operator_turn)])
async def skip_sponsorship_play(
    station_id: uuid.UUID,
    entry_id: uuid.UUID,
    restore: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Skip one coming play of a sponsorship (the control room's Sponsor panel), or put
    it back with ?restore=true. The row stays as "cancelled" so the spot planner does
    not plan that play again; the sponsor's delivery report counts it as missed."""
    from app.services.spot_schedule_service import SAFETY_MARGIN, as_utc
    from app.services.sponsor_item import CANCELLED

    entry = (await db.execute(_station_entry(station_id, entry_id))).scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    if entry.source != "ad_slot" or entry.preempt_at is None:
        raise HTTPException(status_code=422, detail="Only a scheduled sponsorship play can be skipped here")
    if as_utc(entry.preempt_at) <= datetime.now(timezone.utc) + SAFETY_MARGIN:
        raise HTTPException(
            status_code=409,
            detail="It is due within 2 minutes and may already be loading. Remove it from the queue or press Next when it plays.",
        )
    wanted_from, wanted_to = (CANCELLED, "pending") if restore else ("pending", CANCELLED)
    if entry.status != wanted_from:
        raise HTTPException(
            status_code=409,
            detail="It was put back already" if restore else "It is not waiting to play any more",
        )
    entry.status = wanted_to
    await db.commit()
    await _engine_takes_queue_change(db, station_id)
    return {"entry_id": str(entry.id), "status": entry.status}


async def _remove_on_air_locked(db: AsyncSession, station_id, entry: QueueEntry) -> None:
    await db.delete(entry)
    await db.flush()
    still_playing = (await db.execute(
        select(QueueEntry.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .limit(1)
    )).scalar_one_or_none()
    if still_playing is not None:
        await db.commit()
        return
    # During a blackout only its silence may start (same rule as Next)
    blacked_out = await _is_blacked_out(db, station_id)
    next_entry = await _pick_next_pending(db, station_id, datetime.now(timezone.utc), silence_only=blacked_out)
    refilled = False
    if not next_entry and not blacked_out:
        next_entry = await _refill_for_next(db, station_id)
        refilled = True
    if next_entry is None:
        if not refilled:
            await _replenish_queue(db, station_id)
        await db.commit()
        await _broadcast_nothing_playing(station_id)
        return
    mark_started(next_entry, datetime.now(timezone.utc))
    await _drop_timed_twins(db, next_entry)
    next_id = next_entry.id
    await db.commit()
    await _broadcast_now_playing_entry(db, station_id, next_id)
    if not refilled:
        _replenish_soon(station_id)


@router.post("/start")
async def start_playback(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    # During blackout, fill queue with silence entries
    if await _is_blacked_out(db, station_id):
        count = await fill_blackout_queue(db, station_id)
        silence = await db.execute(select(Asset).where(Asset.asset_type == "silence").order_by(Asset.created_at, Asset.id).limit(1))
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
            mark_started(current, datetime.now(timezone.utc))
            await db.commit()
        return {"message": "Already playing", "now_playing": str(current.asset_id)}

    if settings.engine_drives(station_id):
        # The engine pulls its next track by itself; just make sure there is something to pull.
        try:
            await _replenish_queue(db, station_id)
        except Exception as exc:
            logger.error("_replenish_queue failed during start: %s", exc, exc_info=True)
        return {"message": "Playout engine will start the next track", "now_playing": None}

    started = await start_from_queue(db, station_id)
    if started is None:
        return {"message": "Queue empty — no music assets available", "now_playing": None}
    return {"message": "Started", "now_playing": str(started.asset_id)}


async def start_from_queue(db: AsyncSession, station_id) -> QueueEntry | None:
    """Refill the queue (schedule, rules, rotation; any music as a last resort) and
    start its next item. The Start button and the scheduler's dead-air recovery both
    use this, so the queue stays the station's only player. If something started
    meanwhile, that entry is returned and nothing else starts."""
    async with advance_lock(station_id):
        already = (await db.execute(
            select(QueueEntry)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .limit(1)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if already is not None:
            return already
        return await _start_next_locked(db, station_id)


async def _start_next_locked(db: AsyncSession, station_id) -> QueueEntry | None:
    next_entry = await _refill_for_next(db, station_id)
    if not next_entry:
        await db.commit()
        return None
    mark_started(next_entry, datetime.now(timezone.utc))
    started_id = next_entry.id
    await db.commit()
    await _broadcast_now_playing_entry(db, station_id, started_id)
    return next_entry



@router.post("/preview-weather")
async def preview_weather(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    """Generate (or return cached) weather + time TTS for preview — does NOT insert into queue."""
    if not settings.elevenlabs_enabled or not settings.supabase_storage_enabled:
        return JSONResponse({"error": "ElevenLabs or Supabase storage not configured"}, status_code=503)

    from app.services.weather_spot_service import slot_key_for, station_announcement_zone

    now = datetime.now(timezone.utc)
    # The station's own time
    eastern_now = now.astimezone(await station_announcement_zone(db, station_id))
    slot_key = slot_key_for(now, eastern_now.tzinfo)

    from app.services.weather_spot_service import (
        _build_time_text,
        _build_weather_text,
        get_or_create_weather_spot_assets,
        weather_region_for_station,
    )
    from app.services.weather_service import get_current_weather

    region = await weather_region_for_station(db, station_id)
    time_asset, weather_asset = await get_or_create_weather_spot_assets(db, slot_key, region)
    await db.commit()

    # Build text for display
    time_text = _build_time_text(eastern_now, region=region)
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

    from app.services.weather_spot_service import slot_key_for, station_announcement_zone

    now = datetime.now(timezone.utc)
    slot_key = slot_key_for(now, await station_announcement_zone(db, station_id))

    from app.services.weather_spot_service import get_or_create_weather_spot_assets, weather_region_for_station
    time_asset, weather_asset = await get_or_create_weather_spot_assets(
        db, slot_key, await weather_region_for_station(db, station_id)
    )

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
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
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
