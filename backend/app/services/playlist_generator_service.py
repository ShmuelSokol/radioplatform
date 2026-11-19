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

import asyncio
import bisect
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
    MIN_STATION_ID_SECONDS,
    STATION_ID_CATEGORY_PREFIX,
    fit_for_random_pick,
    in_category,
    not_contract_audio,
    not_holiday_only,
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


# Station IDs between songs (automation_config.station_ids): a short jingle
# ("You're listening to Kol B'ramah ...") sprinkled after a song every few minutes,
# never close to a timed break (time check, weather, sponsorships). See
# wiki/playlist-generation.md "Station IDs between songs".
IDS_KEY = "station_ids"
IDS_CATEGORY = "station_id"


@dataclass
class IdSettings:
    enabled: bool = False
    category: str = IDS_CATEGORY
    min_minutes: float = 12.0
    max_minutes: float = 20.0
    # Not within this long of a timed item, before or after it
    clear_minutes: float = 3.0
    # Station's local hours when they play: from start_hour up to end_hour
    # (0-24; equal = all day; start after end runs past midnight)
    start_hour: int = 0
    end_hour: int = 24

    def in_hours(self, local_hour: int) -> bool:
        if self.start_hour == self.end_hour or (self.start_hour, self.end_hour) == (0, 24):
            return True
        if self.start_hour < self.end_hour:
            return self.start_hour <= local_hour < self.end_hour
        return local_hour >= self.start_hour or local_hour < self.end_hour


def id_settings_for(automation_config: dict | None) -> IdSettings:
    raw = (automation_config or {}).get(IDS_KEY) or {}
    if not isinstance(raw, dict):
        return IdSettings()

    def num(name: str, default: float, lo: float, hi: float) -> float:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return min(hi, max(lo, float(value)))

    low = num("min_minutes", 12.0, 3.0, 240.0)
    high = max(low, num("max_minutes", 20.0, 3.0, 240.0))
    return IdSettings(
        enabled=bool(raw.get("enabled", False)),
        category=str(raw.get("category") or IDS_CATEGORY).strip() or IDS_CATEGORY,
        min_minutes=low,
        max_minutes=high,
        clear_minutes=num("clear_minutes", 3.0, 0.0, 15.0),
        start_hour=int(num("start_hour", 0, 0, 24)),
        end_hour=int(num("end_hour", 24, 0, 24)),
    )


# A station ID never follows one of these (two short or spoken breaks back to back)
NOT_BEFORE_AN_ID = {
    "jingle", "spot", "sweeper", "station_id", "time", "weather", "silence",
    "time_announcement", "weather_spot", "zmanim", "announcement", "psa",
}


class StationIdSprinkler:
    """Puts a station ID after a programme item (a song, a story, a shiur) every
    min..max minutes. Used by the playlist fill (`generate`) and the rule fill
    (All Day Stories), both of which walk a `Clock` while they pick."""

    def __init__(self, ids: IdSettings, library: "_Library", blackouts, zone, end: datetime | None = None):
        self.ids = ids
        self.library = library
        self.blackouts = blackouts
        self.zone = zone
        self.end = end
        self.due: datetime | None = None
        self.rows: list = []

    async def prepare(self, start: datetime, queue_starts: list | None = None) -> str | None:
        """Work out when the first ID is due. `queue_starts`: (asset id, expected
        start) of the items already waiting, so an ID waiting in the queue counts
        at its real time. Returns a note when none can play."""
        if not self.ids.enabled:
            return None
        self.rows = await self.library.pool("jingle", self.ids.category)
        if not self.rows:
            return f'Station IDs between songs are on, but no jingle has the category "{self.ids.category}".'
        if self.library.last_used is None:
            await self.library._load_last_used()
        last = max((self.library.last_used.get(str(r.id), _EPOCH) for r in self.rows), default=_EPOCH)
        ids = {str(r.id) for r in self.rows}
        for asset_id, at in queue_starts or ():
            if str(asset_id) in ids:
                last = max(last, as_utc(at))
        # The next one comes a random min..max after the last one aired or was
        # queued; never aired: within the first stretch of the fill
        gap = self._gap()
        self.due = last + gap if last > _EPOCH else start + gap / 2
        return None

    def _gap(self) -> timedelta:
        # Drawn from the first half of min..max: an ID waits for the end of a song and
        # for room clear of breaks, and at air time it can still move a song later, so
        # drawing from the whole range put Main's IDs 25 to 30 minutes apart (2026-11-19)
        low, high = self.ids.min_minutes, self.ids.max_minutes
        return timedelta(minutes=random.uniform(low, low + (high - low) / 2))

    async def after(self, clock: "Clock", previous_type: str | None):
        """Called after each programme item. Returns (row, start) of the drawn ID
        (already played on `clock`) or None."""
        if self.due is None or (previous_type or "music").lower() in NOT_BEFORE_AN_ID:
            return None
        at = clock.start()
        if at < self.due or (self.end is not None and at >= self.end):
            return None
        if any(b.covers(at) for b in self.blackouts):
            return None
        if not self.ids.in_hours(at.astimezone(self.zone).hour):
            return None
        longest = max((float(r.duration or 10.0) for r in self.rows), default=10.0)
        if not clock.clear_of_timed(at, longest, timedelta(minutes=self.ids.clear_minutes)):
            return None  # tried again after the next item
        row, _ = await self.library.draw("jingle", self.ids.category, set(), at)
        if row is None:
            self.due = None
            return None
        clock.play(await self.library.on_air(row))
        self.due = at + self._gap()
        return row, at


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
        # When the last timed item that went by ended (None: none yet)
        self.last_timed_end: datetime | None = None

    def _due(self) -> None:
        while self.timed and self.timed[0].at <= self.at:
            item = self.timed.pop(0)
            self.at = max(self.at, item.at) + timedelta(seconds=item.seconds)
            self.last_timed_end = self.at

    def clear_of_timed(self, at: datetime, seconds: float, margin: timedelta) -> bool:
        """True when an item of `seconds` starting at `at` is at least `margin`
        from the timed items on both sides (the one before has ended `margin`
        earlier, the next starts `margin` after it ends)."""
        if self.last_timed_end is not None and at - self.last_timed_end < margin:
            return False
        upcoming = next((t for t in self.timed if t.at + timedelta(seconds=t.seconds) > at), None)
        if upcoming is not None:
            if upcoming.at <= at:
                return False
            if upcoming.at - (at + timedelta(seconds=seconds)) < margin:
                return False
        return True

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
        # Each type's songs in drawing order (see _order), kept in order as
        # songs are drawn, and how many songs each artist has in the type
        self.orders: dict[tuple, list[tuple]] = {}
        self.cards: dict[tuple, dict[str, tuple]] = {}
        self.artists: dict[tuple, dict] = {}

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
            # The crossfade point comes with the song: reading it for each drawn
            # song was one more database round trip per pick (500 for a redraw).
            analysis = Asset.metadata_extra["audio_analysis"]
            stmt = select(
                Asset.id, Asset.title, Asset.artist, Asset.duration,
                analysis["cross_start_seconds"].astext.label("cross_start"),
                analysis["version"].astext.label("analysis_version"),
            ).where(Asset.asset_type == asset_type)
            if category:
                stmt = stmt.where(in_category(category), not_placeholder(), not_contract_audio(), not_holiday_only())
                if category.lower().startswith(STATION_ID_CATEGORY_PREFIX):
                    stmt = stmt.where(Asset.duration >= MIN_STATION_ID_SECONDS)
            else:
                stmt = stmt.where(fit_for_random_pick())
            # nor an item this station was told not to play ("Not on this station")
            from app.services.station_blocks import blocked_asset_ids

            stmt = stmt.where(Asset.id.notin_(blocked_asset_ids(self.station_id)))
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
        """How long a drawn item holds the air (its crossfade point). A type's
        songs carry it; for a block's own songs it is read for the drawn one."""
        if hasattr(row, "cross_start"):
            analysis = {}
            try:
                if row.cross_start is not None:
                    analysis["cross_start_seconds"] = float(row.cross_start)
                if row.analysis_version is not None:
                    analysis["version"] = int(float(row.analysis_version))
            except (TypeError, ValueError):
                analysis = {}
            return on_air_seconds(row.duration, {"audio_analysis": analysis})
        meta = (
            await self.db.execute(select(Asset.metadata_extra).where(Asset.id == row.id))
        ).scalar_one_or_none()
        return on_air_seconds(row.duration, meta if isinstance(meta, dict) else None)

    def _card(self, row) -> tuple:
        """A song's place in the drawing order: songs not waiting in the queue
        first, then the longest rested; equal ones in a random order."""
        sid = str(row.id)
        return (sid in self.busy, self.last_used.get(sid, _EPOCH), random.random(), sid, _artist_key(row.artist), row)

    def _order(self, key: tuple, rows: list) -> list[tuple]:
        """The type's songs in drawing order. Sorted once and then kept in order
        (mark moves a drawn song), because sorting the whole type again for every
        pick took most of a redraw: 12 s for 730 picks from 8,000-song types, all
        of it on the one thread that also answers the players."""
        if key not in self.orders:
            cards = sorted(self._card(r) for r in rows)
            self.orders[key] = cards
            self.cards[key] = {c[3]: c for c in cards}
            counts: dict = {}
            for c in cards:
                counts[c[4]] = counts.get(c[4], 0) + 1
            self.artists[key] = counts
        return self.orders[key]

    async def draw(self, asset_type, category, recent_artists, at: datetime):
        """Return (row, relaxed) or (None, False) when the type has no songs."""
        if self.last_used is None:
            await self._load_last_used()
        rows = await self.pool(asset_type, category)
        if not rows:
            return None, False
        key = (asset_type, category)
        order = self._order(key, rows)
        # The artist rule comes first, as in the studio program: when the only
        # songs clear of it are already waiting in the queue, one of those is
        # used again rather than putting an artist back too soon. Songs already
        # waiting sort last, so they are reached only when nothing else is clear.
        # Among waiting songs the one drawn longest ago comes first: when a fill
        # runs longer than the type has songs (48 hours from a few hundred),
        # every song is waiting, and treating them all alike brought a song back
        # a few songs after itself.
        counts = self.artists[key]
        clear = len(order) - sum(counts.get(a, 0) for a in recent_artists)
        relaxed = clear <= 0
        skip = () if relaxed else recent_artists
        # Draw from the most-rested third so the order is not the same every cycle...
        limit = max(1, math.ceil((len(order) if relaxed else clear) / 3))
        window: list[tuple] = []
        first = None
        longest = timedelta(0)
        for card in order:
            if card[4] in skip:
                continue
            if first is None:
                first = card
                longest = at - card[1]
            elif card[0] != first[0]:
                # ...never a waiting song while one that is not waiting is clear...
                break
            elif longest > timedelta(0) and at - card[1] < longest / 2:
                # ...and only among songs that have rested about as long as the
                # most rested one. Without this a song that aired an hour ago
                # could be drawn ahead of one that has never aired, just for
                # sitting in the same third.
                break
            window.append(card)
            if len(window) >= limit:
                break
        row = random.choice(window)[5]
        self.mark(row, at)
        return row, relaxed

    def mark(self, row, at: datetime) -> None:
        """A drawn item counts as just used (and waiting in the queue)."""
        sid = str(row.id)
        self.last_used[sid] = at
        self.busy.add(sid)
        for key, cards in self.cards.items():
            old = cards.get(sid)
            if old is None:
                continue
            order = self.orders[key]
            del order[bisect.bisect_left(order, old)]
            new = self._card(old[5])
            bisect.insort(order, new)
            cards[sid] = new

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
    timed_ended: datetime | None = None,
    after_type: str | None = "music",
    queue_starts: list | None = None,
) -> Plan:
    """`timed`: items already queued for set times after `start`, so each pick's
    expected start (and so its block) allows for them. `timed_ended`: when the
    last timed item before `start` ends, and `after_type` the type of the item
    the new picks follow (both for station IDs between songs)."""
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
    if timed_ended is not None:
        clock.last_timed_end = as_utc(timed_ended)
    misses = 0

    from app.services.weather_spot_service import station_announcement_zone

    ids = id_settings_for(station.automation_config)
    sprinkler = StationIdSprinkler(
        ids, library, blackouts, await station_announcement_zone(db, station.id) if ids.enabled else UTC, end
    )
    id_note = await sprinkler.prepare(start, queue_starts)
    if id_note:
        note(id_note)
    previous_type = (after_type or "music").lower()

    # Before and during a holiday, now and then a holiday item instead of an
    # ordinary song or shiur of the same kind (services/holiday_music.py)
    from app.services.holiday_music import HolidayMix

    async def _loaded() -> dict:
        if library.last_used is None:
            await library._load_last_used()
        return library.last_used

    holiday = await HolidayMix.for_station(db, station, start, _loaded)

    def holiday_pick(asset_type: str | None, at: datetime, block_id: str, block_name: str):
        """A holiday item to air instead of the ordinary pick, as a Pick, or None."""
        if holiday is None:
            return None, None
        got = holiday.pick(asset_type, at)
        if got is None:
            return None, None
        row, key = got
        library.mark(row, at)
        return row, Pick(
            at=at, block_id=block_id, block_name=block_name, asset_type=row.asset_type,
            category=row.category, asset_id=row.id, title=row.title, artist=row.artist,
            duration=float(row.duration or DEFAULT_DURATION),
        )

    async def maybe_station_id() -> None:
        """After a programme item: a station ID when one is due and the air is clear."""
        nonlocal previous_type
        got = await sprinkler.after(clock, previous_type)
        if got is None:
            return
        row, at = got
        picks.append(
            Pick(
                at=at, block_id="", block_name="Station ID",
                asset_type="jingle", category=ids.category, asset_id=row.id, title=row.title,
                artist=row.artist, duration=float(row.duration or 10.0),
            )
        )
        previous_type = "jingle"

    while clock.start() < end and len(picks) < MAX_PICKS:
        # Let the players' requests in between picks: nothing below waits on
        # the database any more, and a long fill would hold them all up.
        await asyncio.sleep(0)
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
            previous_type = (picks[-1].asset_type or "music").lower()
            await maybe_station_id()
            continue
        template = block.playlist_template if block is not None and block.playlist_template_id else None
        slots = list(template.slots) if template and template.is_active else []
        if not slots:
            # Keep the station on the air: plain music until a block with a playlist starts
            if block is None:
                note("Some of this time has no schedule block, so any music plays then.")
            else:
                note(f'Block "{block.name}" has no playlist sequence, so any music plays then.')
            row, pick = holiday_pick("music", at, "", block.name if block is not None else "No block")
            if pick is not None:
                picks.append(pick)
                artist = _artist_key(row.artist)
                if artist and artist_distance:
                    recent.append(artist)
                clock.play(await library.on_air(row))
                previous_type = (pick.asset_type or "music").lower()
                await maybe_station_id()
                continue
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
            previous_type = (picks[-1].asset_type or "music").lower()
            await maybe_station_id()
            continue

        key = str(block.id)
        index = int(cursor.get(key, 0)) % len(slots)
        slot = slots[index]
        cursor[key] = (index + 1) % len(slots)
        if (slot.asset_type or "").lower() in NOT_BEFORE_AN_ID and previous_type in NOT_BEFORE_AN_ID:
            # Never two short items back to back (a jingle step right after a station ID)
            misses += 1
            if misses >= len(slots):
                misses = 0
                clock.at = at + SKIP
            continue
        row, pick = holiday_pick(slot.asset_type, at, key, block.name)
        if pick is not None:
            misses = 0
            picks.append(pick)
            artist = _artist_key(row.artist)
            if artist and artist_distance:
                recent.append(artist)
            clock.play(await library.on_air(row))
            previous_type = (pick.asset_type or "music").lower()
            await maybe_station_id()
            continue
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
        previous_type = (picks[-1].asset_type or "music").lower()
        await maybe_station_id()

    if holiday is not None:
        for key, n in holiday.used.items():
            from app.services.holiday_music import HOLIDAY_NAMES

            note(f"{HOLIDAY_NAMES.get(key, key)}: {n} holiday item{'s' if n != 1 else ''} mixed in.")
    for asset_type, category in sorted(library.below_vote, key=str):
        name = f"{asset_type} / {category}" if category else asset_type
        note(f"No {name} has a vote of {min_vote} or more, so the minimum vote was ignored for that type.")
    return Plan(picks=picks, cursor=cursor, notes=notes)
