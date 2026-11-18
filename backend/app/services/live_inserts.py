"""Timed items that still air during a live show (the hourly time check above all).

A live show owns the air, so the queue is paused and hard timed items used to be
lost for as long as the host talked. With the broadcast delay (live_air.py) they can
air on time without cutting the host: the clip plays to listeners at its exact
second and the host's words wait in the delay meanwhile, then air after it. The
delay is a little longer for a while and shrinks back by itself.

A station's `automation_config.live_timed_items` says which items: "time" (default,
only the time check), "all" (every hard timed item: weather, hard sponsorships) or
"none". See wiki/live-shows.md.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.live_air import RATE, Insert, settings_for

logger = logging.getLogger(__name__)

LOOKAHEAD = timedelta(seconds=90)
LATE_LIMIT = timedelta(seconds=20)   # an item this far past its time is not started any more
TIME_CATEGORY = "time_announcement"
MAX_CLIP_SECONDS = 180

# A stream server plays the show from live.mp3 about this far behind the delay's own
# output (its input buffer; harness 2026-11-18: the time check reached the stream 2.1 s
# after its second while a listener page heard it on time). On a station whose
# listeners hear the stream, timed items go into the show this much early.
STREAM_LAG = 2.0

_preparing: set[str] = set()         # entry ids being loaded or already handed to the delay


def _aware(t):
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


async def decode_clip(url: str) -> np.ndarray | None:
    """The audio at `url` as 16-bit mono PCM at RATE (ffmpeg)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-t", str(MAX_CLIP_SECONDS), "-i", url,
            "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), 40)
    except Exception as e:
        logger.warning("Could not decode %s for the live show: %s", url, e)
        return None
    if proc.returncode != 0 or len(out) < 2:
        logger.warning("ffmpeg could not read %s: %s", url, (err or b"")[-200:].decode(errors="replace"))
        return None
    return np.frombuffer(out[: len(out) // 2 * 2], dtype="<i2").copy()


async def _mark_aired(entry_id, station_id, asset_id, seconds: float) -> None:
    from app.db.session import get_async_session
    from app.models.play_log import PlayLog
    from app.models.queue_entry import QueueEntry

    async for db in get_async_session():
        try:
            now = datetime.now(timezone.utc)
            entry = await db.get(QueueEntry, entry_id)
            if entry is not None and entry.status == "pending":
                entry.status = "played"
                entry.started_at = now
            db.add(PlayLog(id=uuid.uuid4(), station_id=station_id, asset_id=asset_id,
                           start_utc=now, end_utc=now + timedelta(seconds=seconds), source="scheduler"))
            await db.commit()
            from app.config import settings
            if settings.engine_drives(station_id):
                # The engine may hold it in its timed lane: it aired here, so it must
                # not air again when the stream comes back after the show
                from app.services import playout_engine
                await playout_engine.engine_timed_drop(station_id, [str(entry_id)])
        except Exception:
            logger.exception("Could not record timed item %s aired during the live show", entry_id)
            await db.rollback()
        break


async def _load(air, entry_id, station_id, asset_id, title: str, kind: str, url: str, at: datetime) -> None:
    pcm = await decode_clip(url)
    if pcm is None or air.closed or air.finishing:
        _preparing.discard(str(entry_id))
        return
    seconds = len(pcm) / RATE

    async def started() -> None:
        await _mark_aired(entry_id, station_id, asset_id, seconds)

    air.add_insert(Insert(key=str(entry_id), title=title, pcm=pcm, air_at=at.timestamp(),
                          kind=kind, on_start=started))
    logger.info("Live show on station %s: '%s' (%.1f s) airs at %s over the delay",
                station_id, title, seconds, at.strftime("%H:%M:%S"))


def stream_lead(station) -> float:
    """Seconds a timed item must enter the show early to reach listeners on time: the
    stream server's lag where listeners hear the stream (`live_stream_lag_seconds` in
    the station's automation_config, default STREAM_LAG), else 0 (listener pages hear
    the delay's output directly)."""
    from app.config import settings
    from app.services import playout_engine

    if not (settings.engine_drives(station.id) and settings.PLAYOUT_STREAM_LIVE):
        return 0.0
    if playout_engine.get_state(str(station.id)).engine_proto < playout_engine.ENGINE_PROTO_LIVE_AUDIO:
        return 0.0
    try:
        lag = float((station.automation_config or {}).get("live_stream_lag_seconds", STREAM_LAG))
    except (TypeError, ValueError):
        lag = STREAM_LAG
    return max(0.0, min(10.0, lag))


async def prepare(db: AsyncSession, station, now: datetime) -> int:
    """Called each scheduler pass while the station has a live show: hand the timed
    items due soon to the station's delay line. Returns how many were taken up."""
    from app.api.v1.live_shows_ws import live_relay
    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry, hard_timed
    from app.services.playout_engine import audio_url

    air = live_relay.airs.get(str(station.id))
    if air is None or not air.listening or air.finishing or air.closed:
        return 0
    _delay, _dump, rule = settings_for(station.automation_config)
    if rule == "none":
        return 0
    rows = (await db.execute(
        select(QueueEntry, Asset)
        .join(Asset, Asset.id == QueueEntry.asset_id)
        .where(
            QueueEntry.station_id == station.id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now + LOOKAHEAD,
            QueueEntry.preempt_at >= now - LATE_LIMIT,
            hard_timed(),
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
        .limit(12)
    )).all()
    lead = timedelta(seconds=stream_lead(station))
    taken = 0
    for entry, asset in rows:
        key = str(entry.id)
        if key in _preparing:
            continue
        is_time = asset.category == TIME_CATEGORY
        if rule == "time" and not is_time:
            continue
        url = audio_url(asset)
        if not url:
            continue
        _preparing.add(key)
        if len(_preparing) > 500:
            _preparing.clear()
            _preparing.add(key)
        taken += 1
        asyncio.ensure_future(_load(air, entry.id, station.id, asset.id, asset.title or "Timed item",
                                    "time" if is_time else "timed", url, _aware(entry.preempt_at) - lead))
    return taken


async def load_filler(air, asset_id: str) -> None:
    """Fetch the station's filler (what plays after a Dump while the delay refills) and
    hand it to the show's delay line. No filler when it cannot be read: the delay then
    builds back by stretching, as it does on a station with none."""
    from app.db.session import get_async_session
    from app.models.asset import Asset
    from app.services.live_air import MAX_DELAY
    from app.services.playout_engine import audio_url

    url, title = None, ""
    async for db in get_async_session():
        try:
            asset = await db.get(Asset, uuid.UUID(str(asset_id)))
            if asset is not None:
                url, title = audio_url(asset), asset.title or "Filler"
        except Exception:
            logger.exception("Could not read the live show filler %s", asset_id)
        break
    if not url:
        logger.warning("Live show filler %s has no audio that can be read", asset_id)
        return
    pcm = await decode_clip(url)
    if pcm is None or air.closed:
        return
    air.filler = pcm[: int(MAX_DELAY * RATE)]
    air.filler_title = title
    logger.info("Live show on station %s: filler '%s' (%.1f s) ready", air.station_id, title, len(air.filler) / RATE)
