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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import (
    Asset,
    fit_for_random_pick,
    in_category,
    not_contract_audio,
    not_placeholder,
    stored_analysis,
)
from app.models.playlist_entry import PlaybackMode, PlaylistEntry
from app.models.schedule import Schedule
from app.models.station import Station
from app.services.scheduling import SchedulingService
from app.services.spot_schedule_service import as_utc, load_blackouts

CONFIG_KEY = "playlist_generation"
DEFAULT_HOURS = 6
DEFAULT_ARTIST_DISTANCE = 2
# A place in the queue is counted as this long when working out when a waiting song airs
QUEUED_PLACE_SECONDS = 240.0
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

    def __init__(
        self, db: AsyncSession, station_id, busy_ids: set, min_vote: int = 0,
        queued: list | None = None, start: datetime | None = None,
    ):
        self.db = db
        self.min_vote = min_vote
        # Types where no song reached the minimum vote, so the minimum was ignored.
        self.below_vote: set[tuple] = set()
        self.station_id = station_id
        self.busy = {str(i) for i in busy_ids}
        # What waits in the queue, in order, and when the new songs start
        self.queued = [str(i) for i in (queued or [])]
        self.start = start
        self.pools: dict[tuple, list] = {}
        self.last_used: dict[str, datetime] | None = None

    async def _load_last_used(self) -> None:
        from app.services.last_played import last_played

        self.last_used = await last_played(self.db, self.station_id)
        # A song waiting in the queue counts as used when it will air (about four
        # minutes a place, counted back from where the new songs start), not when
        # it last aired. Otherwise a song queued an hour before the end that has
        # never aired looks like the most rested one and is drawn again at once.
        if self.start is not None:
            n = len(self.queued)
            for i, sid in enumerate(self.queued):
                due = self.start - timedelta(seconds=QUEUED_PLACE_SECONDS * (n - i))
                if due > self.last_used.get(sid, _EPOCH):
                    self.last_used[sid] = due

    async def pool(self, asset_type: str, category: str | None) -> list:
        key = (asset_type, category)
        if key not in self.pools:
            stmt = select(Asset.id, Asset.title, Asset.artist, Asset.duration).where(
                Asset.asset_type == asset_type
            )
            if category:
                stmt = stmt.where(in_category(category), not_placeholder(), not_contract_audio())
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
        # Longest-rested first. Songs already waiting in the queue go last, and
        # among those the one drawn longest ago comes first: when a fill runs
        # longer than the type has songs (48 hours from a few hundred), every
        # song is waiting, and treating them all alike brought a song back a
        # few songs after itself.

        def rested(row):
            return self.last_used.get(str(row.id), epoch)

        def waiting(row):
            return str(row.id) in self.busy

        ordered = sorted(rows, key=lambda r: (waiting(r), rested(r), random.random()))
        # The artist rule comes first, as in the studio program: when the only
        # songs clear of it are already waiting in the queue, one of those is
        # used again rather than putting an artist back too soon. Songs already
        # waiting sort last, so they are reached only when nothing else is clear.
        clear = [r for r in ordered if _artist_key(r.artist) not in recent_artists]
        relaxed = not clear
        candidates = clear or ordered
        # Draw from the most-rested third so the order is not the same every cycle...
        window = candidates[: max(1, math.ceil(len(candidates) / 3))]
        # ...never a waiting song while one that is not waiting is clear...
        window = [r for r in window if waiting(r) == waiting(window[0])]
        # ...and only among songs that have rested about as long as the most
        # rested one. Without this a song that aired an hour ago could be drawn
        # ahead of one that has never aired, just for sitting in the same third.
        longest = at - rested(window[0])
        if longest > timedelta(0):
            window = [r for r in window if at - rested(r) >= longest / 2]
        row = random.choice(window)
        self.mark(row, at)
        return row, relaxed

    def mark(self, row, at: datetime) -> None:
        """A drawn item counts as just used (and waiting in the queue)."""
        self.last_used[str(row.id)] = at
        self.busy.add(str(row.id))

    def shuffle(self, rows: list, at: datetime):
        """Random among a block's own songs, leaving out the most recently used
        half (aired on this station or already drawn), as the old block player did."""
        epoch = datetime(1970, 1, 1, tzinfo=UTC)

        def used(row):
            # Waiting in the queue counts as more recent than anything aired
            sid = str(row.id)
            return (sid in self.busy, self.last_used.get(sid, epoch))

        unique = list({str(r.id): r for r in rows}.values())
        by_use = sorted(unique, key=used, reverse=True)
        recent_ids = {str(r.id) for r in by_use[: len(unique) // 2] if used(r) > (False, epoch)}
        candidates = [r for r in rows if str(r.id) not in recent_ids] or rows
        return random.choice(candidates)


async def block_songs(db: AsyncSession, schedules) -> dict[str, list]:
    """Songs added straight to a block ("+ Add song"), by block id, in play order.

    Only enabled entries, and never a placeholder or a do_not_play item: no
    automatic pick may use those, even when a person listed them on a block."""
    block_ids = [b.id for s in schedules for b in (s.blocks or [])]
    if not block_ids:
        return {}
    rows = await db.execute(
        select(
            PlaylistEntry.block_id,
            PlaylistEntry.weight,
            Asset.id,
            Asset.title,
            Asset.artist,
            Asset.duration,
            Asset.asset_type,
            Asset.category,
        )
        .join(Asset, Asset.id == PlaylistEntry.asset_id)
        .where(
            PlaylistEntry.block_id.in_(block_ids),
            PlaylistEntry.is_enabled.is_(True),
            not_placeholder(),
        )
        .order_by(PlaylistEntry.block_id, PlaylistEntry.position, PlaylistEntry.created_at)
    )
    songs: dict[str, list] = {}
    for row in rows.all():
        songs.setdefault(str(row.block_id), []).append(row)
    return songs


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _run_start(svc: SchedulingService, block, at: datetime, station) -> datetime:
    """When the run of `block` that is on at `at` began (UTC). A start-only block
    began at its last start; a timed one at the start of the window holding `at`
    (yesterday's window for the part of an overnight block after midnight)."""
    local = svc.station_local(at, station)
    naive = local.replace(tzinfo=None)
    began = None
    if getattr(block, "until_next", False):
        began = svc.last_start(block, local, station)
    else:
        for day in (naive.date(), naive.date() - timedelta(days=1)):
            try:
                lo, hi = svc._block_window(block, day, station)
            except Exception:
                continue
            if lo <= naive < hi:
                began = lo
                break
    if began is None:
        began = datetime.combine(naive.date(), datetime.min.time())
    return began.replace(tzinfo=local.tzinfo).astimezone(UTC)


def _mode(block) -> PlaybackMode:
    mode = getattr(block, "playback_mode", None) or PlaybackMode.SEQUENTIAL
    try:
        return PlaybackMode(mode)
    except ValueError:
        return PlaybackMode.SEQUENTIAL


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
    queued: list | None = None,
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
    songs_by_block = await block_songs(db, schedules)
    svc = SchedulingService(db)
    library = _Library(db, station.id, busy_ids or set(), min_vote, queued, start)
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
        own = songs_by_block.get(str(block.id)) if block is not None else None
        named = block.playlist_template if block is not None and block.playlist_template_id else None
        both = bool(own and named is not None and named.is_active and named.slots)
        if both:
            # Both: the songs added by hand play first, each once per run of the
            # block, then the block's playlist fills the rest (Shmuel, 2026-11-17).
            if library.last_used is None:
                await library._load_last_used()
            began = _run_start(svc, block, at, station)
            own = [
                r for r in own
                if library.last_used.get(str(r.id), _EPOCH) < began
                and not (str(r.id) in library.busy and began <= start)
            ]
        if own:
            # The block's own songs, in the block's playback mode.
            if library.last_used is None:
                await library._load_last_used()
            key = str(block.id)
            mode = _mode(block)
            if mode == PlaybackMode.SHUFFLE:
                row = library.shuffle(own, at)
            elif mode == PlaybackMode.WEIGHTED:
                row = random.choices(own, weights=[max(int(r.weight or 1), 1) for r in own], k=1)[0]
            elif both:
                row = own[0]  # in the block's order; the playlist's cursor is left alone
            else:
                index = int(cursor.get(key, 0)) % len(own)
                row = own[index]
                cursor[key] = (index + 1) % len(own)
            library.mark(row, at)
            picks.append(
                Pick(
                    at=at, block_id=key, block_name=block.name, asset_type=row.asset_type,
                    category=row.category, asset_id=row.id, title=row.title, artist=row.artist,
                    duration=float(row.duration or DEFAULT_DURATION),
                )
            )
            artist = _artist_key(row.artist)
            if artist and artist_distance:
                recent.append(artist)
            clock.play(await library.on_air(row))
            continue
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
