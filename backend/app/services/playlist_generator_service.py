"""
Playlist generation ahead of time, the way the studio program does it.

Each schedule block names a playlist template: a repeating sequence of song
types ("purim, 1 med fast, *NEWHIT*, ..."). The generator walks forward from the
end of the queue, asks the scheduler's own resolver which block is on at that
moment, takes the next type in that block's sequence and draws a song of that
type: the one that has gone longest without airing, never the same artist
within `artist_distance` songs.

See wiki/playlist-generation.md.
"""

import math
import random
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.schedule import Schedule
from app.models.station import Station
from app.services.scheduling import SchedulingService
from app.services.spot_schedule_service import as_utc, load_blackouts

CONFIG_KEY = "playlist_generation"
DEFAULT_HOURS = 6
DEFAULT_ARTIST_DISTANCE = 2
DEFAULT_DURATION = 180.0
SKIP = timedelta(minutes=15)
MAX_PICKS = 4000


@dataclass
class Settings:
    enabled: bool = False
    hours: int = DEFAULT_HOURS
    artist_distance: int = DEFAULT_ARTIST_DISTANCE


def settings_for(automation_config: dict | None) -> Settings:
    raw = (automation_config or {}).get(CONFIG_KEY) or {}
    return Settings(
        enabled=bool(raw.get("enabled", False)),
        hours=max(1, min(48, int(raw.get("hours", DEFAULT_HOURS) or DEFAULT_HOURS))),
        artist_distance=max(0, min(20, int(raw.get("artist_distance", DEFAULT_ARTIST_DISTANCE)))),
    )


@dataclass
class Pick:
    at: datetime  # UTC, when it is expected to start
    block_id: str
    block_name: str
    asset_type: str
    category: str | None
    asset_id: object
    title: str
    artist: str | None
    duration: float
    # True when no song of the type was clear of the artist rule, so the rule gave way
    artist_rule_relaxed: bool = False


@dataclass
class Plan:
    picks: list[Pick]
    cursor: dict  # block id -> next position in its sequence
    notes: list[str]


def _artist_key(artist: str | None) -> str | None:
    key = (artist or "").strip().lower()
    return key or None


class _Library:
    """Songs of each type, with when each last aired on this station."""

    def __init__(self, db: AsyncSession, station_id, busy_ids: set):
        self.db = db
        self.station_id = station_id
        self.busy = {str(i) for i in busy_ids}
        self.pools: dict[tuple, list] = {}
        self.last_used: dict[str, datetime] | None = None

    async def _load_last_used(self) -> None:
        rows = await self.db.execute(
            select(PlayLog.asset_id, func.max(PlayLog.start_utc))
            .where(PlayLog.station_id == self.station_id, PlayLog.asset_id.isnot(None))
            .group_by(PlayLog.asset_id)
        )
        self.last_used = {str(a): as_utc(t) for a, t in rows.all() if t is not None}

    async def pool(self, asset_type: str, category: str | None) -> list:
        key = (asset_type, category)
        if key not in self.pools:
            stmt = select(Asset.id, Asset.title, Asset.artist, Asset.duration).where(
                Asset.asset_type == asset_type
            )
            if category:
                stmt = stmt.where(Asset.category == category)
            else:
                stmt = stmt.where((Asset.category != "do_not_play") | (Asset.category.is_(None)))
            self.pools[key] = list((await self.db.execute(stmt)).all())
        return self.pools[key]

    async def draw(self, asset_type, category, recent_artists, at: datetime):
        """Return (row, relaxed) or (None, False) when the type has no songs."""
        if self.last_used is None:
            await self._load_last_used()
        rows = await self.pool(asset_type, category)
        if not rows:
            return None, False
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        # Longest-rested first. Songs already waiting in the queue go last.
        far_future = datetime(9999, 1, 1, tzinfo=UTC)

        def rested(row):
            sid = str(row.id)
            if sid in self.busy:
                return far_future
            return self.last_used.get(sid, epoch)

        ordered = sorted(rows, key=lambda r: (rested(r), random.random()))
        clear = [r for r in ordered if _artist_key(r.artist) not in recent_artists]
        relaxed = not clear
        candidates = clear or ordered
        # Draw from the most-rested third so the order is not the same every cycle.
        window = candidates[: max(1, math.ceil(len(candidates) / 3))]
        # ...but never reach into songs that are already queued while rested ones remain
        fresh = [r for r in window if str(r.id) not in self.busy]
        row = random.choice(fresh or window)
        self.last_used[str(row.id)] = at
        self.busy.add(str(row.id))
        return row, relaxed


async def generate(
    db: AsyncSession,
    station: Station,
    start: datetime,
    hours: float,
    *,
    artist_distance: int = DEFAULT_ARTIST_DISTANCE,
    cursor: dict | None = None,
    recent_artists: list[str | None] | None = None,
    busy_ids: set | None = None,
) -> Plan:
    start = as_utc(start)
    end = start + timedelta(hours=hours)
    cursor = dict(cursor or {})
    notes: list[str] = []

    schedules = (
        (
            await db.execute(
                select(Schedule)
                .where(Schedule.station_id == station.id, Schedule.is_active.is_(True))
                .options(selectinload(Schedule.blocks))
            )
        )
        .scalars()
        .all()
    )
    blackouts = await load_blackouts(db, station.id, start, end)
    svc = SchedulingService(db)
    library = _Library(db, station.id, busy_ids or set())
    recent: deque = deque(
        [k for k in (_artist_key(a) for a in (recent_artists or [])) if k],
        maxlen=artist_distance or 1,
    )
    if not artist_distance:
        recent.clear()

    def note(text: str) -> None:
        if text not in notes:
            notes.append(text)

    picks: list[Pick] = []
    at = start
    misses = 0
    while at < end and len(picks) < MAX_PICKS:
        off = next((b for b in blackouts if b.covers(at)), None)
        if off is not None:
            note(f"Off the air for {off.name}: nothing generated for that time.")
            at = off.end
            continue
        ranked = svc.rank_matching_blocks(schedules, at, station)
        if not ranked:
            note("Some of this time has no schedule block, so nothing is generated for it.")
            at += SKIP
            continue
        _, block = ranked[0]
        template = block.playlist_template if block.playlist_template_id else None
        slots = list(template.slots) if template and template.is_active else []
        if not slots:
            note(f'Block "{block.name}" has no playlist sequence, so nothing is generated for it.')
            at += SKIP
            continue

        key = str(block.id)
        index = int(cursor.get(key, 0)) % len(slots)
        slot = slots[index]
        cursor[key] = (index + 1) % len(slots)
        row, relaxed = await library.draw(
            slot.asset_type, slot.category, set(recent) if artist_distance else set(), at
        )
        if row is None:
            what = f"{slot.asset_type} / {slot.category}" if slot.category else slot.asset_type
            note(f'No audio of type "{what}" in the library ("{template.name}" skips it).')
            misses += 1
            if misses >= len(slots):
                misses = 0
                at += SKIP
            continue
        misses = 0
        duration = float(row.duration or DEFAULT_DURATION)
        picks.append(
            Pick(
                at=at,
                block_id=key,
                block_name=block.name,
                asset_type=slot.asset_type,
                category=slot.category,
                asset_id=row.id,
                title=row.title,
                artist=row.artist,
                duration=duration,
                artist_rule_relaxed=relaxed and bool(_artist_key(row.artist)),
            )
        )
        artist = _artist_key(row.artist)
        if artist and artist_distance:
            recent.append(artist)
        at += timedelta(seconds=duration)

    return Plan(picks=picks, cursor=cursor, notes=notes)
