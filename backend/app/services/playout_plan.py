"""What a local FM box needs to keep the station on air with no internet.

The box (a small computer beside the transmitter) runs the same playout engine as the
stream server, with a relay in front of it (`infra/local/kbr_relay.py`). While the
internet is up the relay passes the engine's calls on to this backend. It also fetches
this plan every minute and keeps the audio on its own disk, so when the line drops it
can answer the engine itself: the queue as it stood, timed items at their second, and
silence inside a blackout. See `wiki/local-fm-box.md`.

Read-only: nothing here changes the queue.
"""

import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.asset import Asset, not_placeholder
from app.models.queue_entry import QueueEntry, is_soft_timed
from app.services import playout_engine
from app.services.station_blocks import blocked_asset_ids
from app.services.station_id_guard import is_auto_station_id

# Songs ahead that go in the plan. At about 4 minutes each this is well past two days;
# timed items are cut off by the hours asked for instead.
PLAN_MAX_ENTRIES = 1500
LIBRARY_PAGE = 500
# A box asks for this many days beyond the queue (`?days=`): the queue holds about two
# days, the box must carry the station for a week without the internet.
MAX_EXTENSION_DAYS = 14
# Shabbos and Yom Tov windows the box keeps, well past the days it plans
BLACKOUT_DAYS = 30
# The engine id written into a music template; the box puts its own id in its place
TEMPLATE_ENTRY = uuid.UUID(int=0)
# One station's extension is worked out at most this often (it plans a week of blocks
# and sponsorships); a box asks every few minutes
EXTENSION_CACHE_SECONDS = 300.0
_extension_cache: dict[tuple, tuple[float, dict]] = {}


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


async def build_plan(db: AsyncSession, station_id, hours: float = 48.0, days: float | None = None) -> dict:
    """The pending queue in play order with everything the engine needs for each item,
    and the blackout windows that touch the period.

    `days`: a box asks for the days after the queue too (`extension`, see
    build_extension): the station's timed items worked out as the site would queue
    them, the songs it plays at each hour of the day, and a month of Shabbos and Yom Tov."""
    from app.api.v1.queue import OPERATOR_SOURCES
    from app.services import long_items
    from app.services.spot_schedule_service import load_blackouts

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=hours)
    # Same rule as /next: a placeholder or do-not-play item queued automatically never airs
    may_air = or_(
        QueueEntry.source.in_(OPERATOR_SOURCES),
        and_(
            QueueEntry.asset_id.notin_(select(Asset.id).where(~not_placeholder())),
            # nor an item this station alone was told not to play
            QueueEntry.asset_id.notin_(blocked_asset_ids(station_id)),
        ),
    )
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= horizon),
            may_air,
        )
        .order_by(QueueEntry.position)
        .limit(PLAN_MAX_ENTRIES)
    )
    entries = []
    for entry in res.unique().scalars().all():
        asset = entry.asset
        url = playout_engine.audio_url(asset)
        if not url:
            continue
        timed = entry.preempt_at is not None
        hard = timed and not is_soft_timed(entry)
        entries.append({
            "entry_id": str(entry.id),
            "position": entry.position,
            "at": _epoch(entry.preempt_at),
            "mode": ("hard" if hard else "soft") if timed else None,
            "hard_by": _epoch(entry.hard_by),
            "kind": (asset.asset_type or "").lower(),
            "title": asset.title or "",
            "duration": asset.duration,
            "url": url,
            # Sent up with a start report the box kept: the site logs the play even when
            # the entry was removed there meanwhile
            "asset_id": str(asset.id),
            # A shiur or story: paused for a hard item and carried on after (long_items)
            "long": long_items.is_long_form(asset),
            # An automatic station ID: the box keeps it away from breaks, as the site does
            "station_id": is_auto_station_id(entry),
            # The same strings /next and the timed hand-over would give the engine
            "uri_main": playout_engine.build_uri(entry, "main"),
            "uri_timed": playout_engine.build_uri(entry, "timed") if hard else None,
        })
    blackouts = await load_blackouts(db, station_id, now - timedelta(hours=1), horizon)
    from app.models.station import Station

    station = await db.get(Station, station_id)
    plan = {
        "station_id": str(station_id),
        "generated_at": now.timestamp(),
        "hours": hours,
        "timezone": (station.timezone if station else None) or "America/New_York",
        "station_name": (station.name if station else None) or "",
        # What a hard item does to a shiur on air: pause (default), wait or cut
        "long_items": long_items.policy(station),
        "entries": entries,
        "blackouts": [
            {"start": b.start.timestamp(), "end": b.end.timestamp(), "name": b.name}
            for b in blackouts
        ],
    }
    if days and station is not None:
        days = max(1.0, min(float(days), MAX_EXTENSION_DAYS))
        key = (str(station_id), days)
        hit = _extension_cache.get(key)
        if hit and time.monotonic() - hit[0] < EXTENSION_CACHE_SECONDS:
            plan["extension"] = hit[1]
        else:
            plan["extension"] = await build_extension(db, station, entries, days, now)
            _extension_cache[key] = (time.monotonic(), plan["extension"])
    return plan


def _projected_id(station_id, at: datetime, label: str, index: int, asset_id) -> str:
    """A stable id for an item worked out ahead (the same on every fetch), so the box
    remembers it aired. Not a queue row: its start report is logged by asset."""
    key = f"{station_id}|{at.isoformat()}|{label}|{index}|{asset_id}"
    return str(uuid.UUID(hashlib.sha1(key.encode()).hexdigest()[:32]))


def _entry_dict(entry, asset, at: datetime | None, mode: str | None) -> dict | None:
    from app.services import long_items

    url = playout_engine.audio_url(asset)
    if not url:
        return None
    return {
        "entry_id": str(entry.id),
        "position": None,
        "at": _epoch(at),
        "mode": mode,
        "hard_by": _epoch(entry.hard_by),
        "kind": (asset.asset_type or "").lower(),
        "title": asset.title or "",
        "duration": asset.duration,
        "url": url,
        "asset_id": str(asset.id),
        "long": long_items.is_long_form(asset),
        "uri_main": playout_engine.build_uri(entry, "main"),
        "uri_timed": playout_engine.build_uri(entry, "timed") if mode == "hard" else None,
        "projected": True,
    }


async def build_extension(db: AsyncSession, station, queued: list[dict], days: float, now: datetime) -> dict:
    """What a box needs to carry the station past the end of its queue, for `days`.

    * `timed`: the program blocks (time checks, sponsorship places, shiurim) and the
      built-in hourly time check and weather the site would queue after the queue's
      own reach, worked out the same way. Only recordings that exist now are used:
      the hour's own time clip, and the day's weather report only when one was made
      for that date (the weather service's forecast days); a day with none has no
      weather rather than old weather. A shiur already in the queue or earlier in
      the week is not repeated (each day's Daf Yomi arrives with the internet).
    * `music`: the station's songs by hour of its own day (what it queued or played
      at that hour over the last week), for the box to draw from when the queue runs out.
    * `blackouts`: Shabbos and Yom Tov for BLACKOUT_DAYS.
    """
    from app.models.asset import Asset, not_placeholder
    from app.models.play_log import PlayLog
    from app.models.queue_entry import QueueEntry
    from app.services import long_items
    from app.services.program_block_service import (
        HORIZON_HOURS,
        blocks_keep_hourly,
        load_blocks,
        plan_blocks,
    )
    from app.services.spot_schedule_service import default_timing, load_blackouts, station_tz
    from app.services.weather_spot_service import (
        find_time_clip_asset,
        find_weather_spot_asset,
        half_hour_time_check,
        station_weather_region,
    )
    from app.services.queue_replenish_service import local_hour_marks

    zone = station_tz(station.timezone)
    config = station.automation_config or {}
    end = now + timedelta(days=days)
    # The queue's own timed items reach this far (blocks, time and weather: 48 h)
    reach = now + timedelta(hours=HORIZON_HOURS)
    queued_minutes = {
        round(e["at"] / 60) for e in queued if e.get("at") is not None
    }
    queued_long = {e["asset_id"] for e in queued if e.get("long")}
    blackouts = await load_blackouts(db, station.id, now - timedelta(hours=1), now + timedelta(days=BLACKOUT_DAYS))

    def off_air(at: datetime) -> bool:
        return any(b.covers(at) for b in blackouts)

    assets: dict[str, Asset | None] = {}

    async def asset(asset_id) -> Asset | None:
        key = str(asset_id)
        if key not in assets:
            assets[key] = await db.get(Asset, asset_id)
        return assets[key]

    timed: list[dict] = []
    long_seen = set(queued_long)

    def add(at: datetime, label: str, index: int, a: Asset, mode: str, source: str, hard_by=None):
        entry = QueueEntry(
            id=uuid.UUID(_projected_id(station.id, at, label, index, a.id)), station_id=station.id,
            asset_id=a.id, position=0, status="pending", source=source, preempt_at=at,
            timing_mode=mode, hard_by=hard_by,
        )
        entry.asset = a
        got = _entry_dict(entry, a, at, mode)
        if got:
            got["label"] = label
            timed.append(got)

    blocks = await load_blocks(db, station.id)
    region = station_weather_region(config)
    wants_hourly = bool(config.get("hourly_time_announcement") or config.get("weather_enabled"))
    if wants_hourly and (not blocks or blocks_keep_hourly(config)):
        # The built-in hourly time check (and its weather), as the replenisher queues it
        half_on = half_hour_time_check(config)
        weather_on = bool(config.get("weather_enabled"))
        half_weather = weather_on and int(config.get("weather_interval_minutes", 30) or 0) == 30
        clips: dict[tuple, Asset | None] = {}
        weather: dict[str, Asset | None] = {}
        for mark in local_hour_marks(reach, zone, int((end - reach).total_seconds() // 3600) + 1):
            for at, minute in ((mark, 0), (mark + timedelta(minutes=30), 30)):
                if at <= reach or at > end or off_air(at) or round(at.timestamp() / 60) in queued_minutes:
                    continue
                if minute == 30 and not (half_on or half_weather):
                    continue
                local = at.astimezone(zone)
                mode = default_timing(local.minute, config.get("spot_timing"))
                items = []
                if minute == 0 or half_on:
                    key = (local.hour, minute)
                    if key not in clips:
                        clips[key] = await find_time_clip_asset(db, local.hour, region, minute)
                    if clips[key] is not None:
                        items.append(clips[key])
                if weather_on and (minute == 0 or half_weather) and (minute == 0 or items or not half_on):
                    day = local.strftime("%Y-%m-%d")
                    if day not in weather:
                        weather[day] = await find_weather_spot_asset(db, day, region)
                    if weather[day] is not None:
                        items.append(weather[day])
                for i, a in enumerate(items):
                    add(at, "hourly", i, a, mode, "auto")

    if blocks:
        occurrences = await plan_blocks(
            db, station, hours=(end - now).total_seconds() / 3600, now=now, dry_run=False,
            blocks=blocks, prepare=False,
        )
        for occ in occurrences:
            at = occ.at.replace(second=0, microsecond=0)
            if at <= reach - timedelta(minutes=30) or round(at.timestamp() / 60) in queued_minutes:
                continue
            got = [(i, item) for i, item in enumerate(occ.items) if item.asset_id is not None]
            # The block's programme is a shiur already queued or earlier this week: the
            # site gets the next day's from the internet. Left out, with what announces it.
            recordings = [item for _, item in got if item.kind in ("asset", "category")]
            if recordings:
                last = await asset(recordings[-1].asset_id)
                if last is not None and long_items.is_long_form(last):
                    if str(last.id) in long_seen:
                        continue
                    long_seen.add(str(last.id))
            from app.services.program_block_service import hard_by as block_hard_by

            for i, item in got:
                a = await asset(item.asset_id)
                if a is not None:
                    add(at, occ.block.name, i, a, occ.block.timing_mode or "hard", "block",
                        block_hard_by(occ.block, at))

    timed.sort(key=lambda e: (e["at"], e["label"] != "hourly", e["label"]))

    # Songs by hour of the station's day: what it queued or played at that hour
    music: dict[str, list[str]] = {str(h): [] for h in range(24)}
    library: dict[str, dict] = {}
    may_air = select(Asset.id).where(not_placeholder())
    t = now
    for e in queued:
        if e.get("at") is None:
            if e.get("kind") == "music":
                music[str(t.astimezone(zone).hour)].append(e["asset_id"])
            t += timedelta(seconds=float(e.get("duration") or 180))
    played = await db.execute(
        select(PlayLog.asset_id, PlayLog.start_utc)
        .join(Asset, Asset.id == PlayLog.asset_id)
        .where(PlayLog.station_id == station.id, PlayLog.start_utc >= now - timedelta(days=8),
               Asset.asset_type == "music", Asset.id.in_(may_air))
    )
    for asset_id, start in played.all():
        start = start if start.tzinfo else start.replace(tzinfo=UTC)
        music[str(start.astimezone(zone).hour)].append(str(asset_id))
    for hour, ids in music.items():
        music[hour] = list(dict.fromkeys(ids))
    from app.services.station_blocks import blocked_asset_ids

    wanted = {i for ids in music.values() for i in ids}
    if wanted:
        rows = await db.execute(
            select(Asset).where(
                Asset.id.in_([uuid.UUID(i) for i in wanted]), not_placeholder(),
                Asset.id.notin_(blocked_asset_ids(station.id)),
            )
        )
        for a in rows.scalars().all():
            entry = QueueEntry(id=TEMPLATE_ENTRY, station_id=station.id, asset_id=a.id, position=0,
                               status="pending", source="auto")
            entry.asset = a
            uri = playout_engine.build_uri(entry, "main")
            if uri:
                library[str(a.id)] = {
                    "title": a.title or "", "duration": a.duration, "kind": "music",
                    "url": playout_engine.audio_url(a), "uri": uri,
                }
    for hour in music:
        music[hour] = [i for i in music[hour] if i in library]

    # Station IDs between songs (automation_config.station_ids): the box puts one after
    # a song every so often past the end of its queue, as the site's fill does
    from app.services.playlist_generator_service import id_settings_for

    ids_cfg = id_settings_for(config)
    id_list: list[str] = []
    if ids_cfg.enabled:
        from app.models.asset import (
            MIN_STATION_ID_SECONDS, STATION_ID_CATEGORY_PREFIX, in_category, not_contract_audio,
            not_holiday_only,
        )

        stmt = select(Asset).where(
            Asset.asset_type == "jingle", in_category(ids_cfg.category), not_placeholder(),
            not_contract_audio(), not_holiday_only(), Asset.id.notin_(blocked_asset_ids(station.id)),
        )
        if ids_cfg.category.lower().startswith(STATION_ID_CATEGORY_PREFIX):
            stmt = stmt.where(Asset.duration >= MIN_STATION_ID_SECONDS)
        for a in (await db.execute(stmt.limit(200))).scalars().all():
            entry = QueueEntry(id=TEMPLATE_ENTRY, station_id=station.id, asset_id=a.id, position=0,
                               status="pending", source="auto")
            entry.asset = a
            uri = playout_engine.build_uri(entry, "main")
            if uri:
                library[str(a.id)] = {
                    "title": a.title or "", "duration": a.duration, "kind": "jingle",
                    "url": playout_engine.audio_url(a), "uri": uri, "station_id": True,
                }
                id_list.append(str(a.id))
    return {
        "days": days,
        "until": end.timestamp(),
        "generated_at": now.timestamp(),
        "timed": timed,
        "music": music,
        "songs": library,
        "template_entry": str(TEMPLATE_ENTRY),
        "ids": {
            "enabled": bool(id_list), "assets": id_list,
            "min_minutes": ids_cfg.min_minutes, "max_minutes": ids_cfg.max_minutes,
            "clear_minutes": ids_cfg.clear_minutes,
            "start_hour": ids_cfg.start_hour, "end_hour": ids_cfg.end_hour,
        },
        "blackouts": [
            {"start": b.start.timestamp(), "end": b.end.timestamp(), "name": b.name}
            for b in blackouts
        ],
    }


async def library_page(db: AsyncSession, after: str | None = None, limit: int = LIBRARY_PAGE) -> dict:
    """One page of the whole library's audio addresses, by id, for the box's full copy.
    `next` is the `after` of the following page, or None on the last one."""
    limit = max(1, min(int(limit), 2000))
    stmt = (
        select(Asset.id, Asset.file_path, Asset.asset_type, Asset.title, Asset.artist, Asset.duration)
        .order_by(Asset.id).limit(limit)
    )
    if after:
        import uuid

        stmt = stmt.where(Asset.id > uuid.UUID(after))
    rows = (await db.execute(stmt)).all()
    items = []
    for asset_id, file_path, asset_type, title, artist, duration in rows:
        url = playout_engine.audio_url(_PathOnly(file_path))
        if url:
            # title, artist and length: the box's own control room searches them offline
            items.append({"asset_id": str(asset_id), "url": url, "kind": (asset_type or "").lower(),
                          "title": title or "", "artist": artist or "", "duration": duration})
    return {"items": items, "next": str(rows[-1][0]) if len(rows) == limit else None}


class _PathOnly:
    def __init__(self, file_path):
        self.file_path = file_path
