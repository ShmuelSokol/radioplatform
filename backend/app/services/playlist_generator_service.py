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

from app.models.asset import Asset, fit_for_random_pick, not_placeholder, stored_analysis
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
    min_vote: int = 0


def settings_for(automation_config: dict | None) -> Settings:
    raw = (automation_config or {}).get(CONFIG_KEY) or {}
    return Settings(
        enabled=bool(raw.get("enabled", False)),
        hours=max(1, min(48, int(raw.get("hours", DEFAULT_HOURS) or DEFAULT_HOURS))),
        artist_distance=max(0, min(20, int(raw.get("artist_distance", DEFAULT_ARTIST_DISTANCE)))),
        min_vote=max(0, min(100, int(raw.get("min_vote", 0) or 0))),
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


def on_air_seconds(duration: float | None, metadata_extra: dict | None) -> float:
    """How long an item holds the air: up to where the next one crossfades in
    (cross_start, default 3 s before the end), the same rule the scheduler uses
    to change songs (queue.switch_time). Counting full lengths put a block's
    first song early by about 3 s for every song queued before it."""
    length = float(duration or DEFAULT_DURATION)
    cross = 3.0
    analysis = stored_analysis(metadata_extra)
    cross_start = analysis.get("cross_start_seconds")
    if isinstance(cross_start, (int, float)) and 0 < cross_start < length:
        cross = length - cross_start
    return length - min(cross, max(0.0, length / 2))


@dataclass
class TimedItem:
    """Something queued for a set time (time check, weather, sponsor place)."""

    at: datetime  # UTC
    seconds: float  # how long it holds the air
    hard: bool  # True: the item on air is faded at `at`; False: it waits for the item to end


class Clock:
    """Walks the expected air time forward, the way the queue really plays:
    a soft timed item goes on after the item on air whose end passes its time,
    a hard one cuts the item on air at its time."""

    def __init__(self, at: datetime, timed: list[TimedItem] | None = None):
        self.at = at
        self.timed = sorted(timed or [], key=lambda t: t.at)

    def _due(self) -> None:
        while self.timed and self.timed[0].at <= self.at:
            item = self.timed.pop(0)
            self.at = max(self.at, item.at) + timedelta(seconds=item.seconds)

    def start(self) -> datetime:
        """When the next ordinary item goes on air."""
        self._due()
        return self.at

    def play(self, seconds: float) -> None:
        """An ordinary item that starts now and holds the air for `seconds`."""
        self._due()
        end = self.at + timedelta(seconds=seconds)
        cut = next((t for t in self.timed if t.hard and self.at < t.at < end), None)
        self.at = cut.at if cut is not None else end
        self._due()


def _artist_key(artist: str | None) -> str | None:
    key = (artist or "").strip().lower()
    return key or None


class _Library:
    """Songs of each type, with when each last aired on this station."""

    def __init__(self, db: AsyncSession, station_id, busy_ids: set, min_vote: int = 0):
        self.db = db
        self.min_vote = min_vote
        # Types where no song reached the minimum vote, so the minimum was ignored.
        self.below_vote: set[tuple] = set()
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
                stmt = stmt.where(Asset.category == category, not_placeholder())
            else:
                stmt = stmt.where(fit_for_random_pick())
            rows = []
            if self.min_vote:
                rows = list((await self.db.execute(stmt.where(Asset.vote >= self.min_vote))).all())
            if not rows:
                rows = list((await self.db.execute(stmt)).all())
                if self.min_vote and rows:
                    self.below_vote.add(key)
            self.pools[key] = rows
        return self.pools[key]

    async def on_air(self, row) -> float:
        """How long a drawn item holds the air (its crossfade point). The pools
        leave out the audio analysis, so it is read for the drawn item only."""
        meta = (
            await self.db.execute(select(Asset.metadata_extra).where(Asset.id == row.id))
        ).scalar_one_or_none()
        return on_air_seconds(row.duration, meta if isinstance(meta, dict) else None)

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
        # The artist rule comes first, as in the studio program: when the only
        # songs clear of it are already waiting in the queue, one of those is
        # used again rather than putting an artist back too soon. Songs already
        # waiting sort last, so they are reached only when nothing else is clear.
        clear = [r for r in ordered if _artist_key(r.artist) not in recent_artists]
        relaxed = not clear
        candidates = clear or ordered
        # Draw from the most-rested third so the order is not the same every cycle...
        window = candidates[: max(1, math.ceil(len(candidates) / 3))]
        # ...but only among songs that have rested about as long as the most
        # rested one. Without this a song that aired an hour ago could be drawn
        # ahead of one that has never aired, just for sitting in the same third.
        longest = at - rested(window[0])
        if longest > timedelta(0):
            window = [r for r in window if at - rested(r) >= longest / 2]
        row = random.choice(window)
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
    min_vote: int = 0,
    cursor: dict | None = None,
    recent_artists: list[str | None] | None = None,
    busy_ids: set | None = None,
    timed: list[TimedItem] | None = None,
) -> Plan:
    """`timed`: items already queued for set times after `start`, so each pick's
    expected start (and so its block) allows for them."""
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
    library = _Library(db, station.id, busy_ids or set(), min_vote)
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
    clock = Clock(start, timed)
    misses = 0
    while clock.start() < end and len(picks) < MAX_PICKS:
        at = clock.start()
        off = next((b for b in blackouts if b.covers(at)), None)
        if off is not None:
            note(f"Off the air for {off.name}: nothing generated for that time.")
            clock.at = off.end
            continue
        ranked = svc.rank_matching_blocks(schedules, at, station)
        block = ranked[0][1] if ranked else None
        template = block.playlist_template if block is not None and block.playlist_template_id else None
        slots = list(template.slots) if template and template.is_active else []
        if not slots:
            # Keep the station on the air: plain music until a block with a playlist starts
            if block is None:
                note("Some of this time has no schedule block, so any music plays then.")
            else:
                note(f'Block "{block.name}" has no playlist sequence, so any music plays then.')
            row, relaxed = await library.draw(
                "music", None, set(recent) if artist_distance else set(), at
            )
            if row is None:
                clock.at = at + SKIP
                continue
            duration = float(row.duration or DEFAULT_DURATION)
            picks.append(
                Pick(
                    at=at, block_id="", block_name=block.name if block is not None else "No block",
                    asset_type="music", category=None, asset_id=row.id, title=row.title,
                    artist=row.artist, duration=duration,
                    artist_rule_relaxed=relaxed and bool(_artist_key(row.artist)),
                )
            )
            artist = _artist_key(row.artist)
            if artist and artist_distance:
                recent.append(artist)
            clock.play(await library.on_air(row))
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
                clock.at = at + SKIP
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
        clock.play(await library.on_air(row))

    for asset_type, category in sorted(library.below_vote, key=str):
        name = f"{asset_type} / {category}" if category else asset_type
        note(f"No {name} has a vote of {min_vote} or more, so the minimum vote was ignored for that type.")
    return Plan(picks=picks, cursor=cursor, notes=notes)
