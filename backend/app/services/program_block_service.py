"""
Program blocks: named runs of items that air as one event at set clock times.

The studio program has lines such as "Weather Sponsors" that expand into a
station ID, the weather, a PSA, a sponsor spot and a programme segment. A
ProgramBlock is that line. This module turns blocks into concrete occurrences
and keeps the station queue's pending entries (source="block") in step.

A "sponsors" item is a number of sponsor places. The spot planner
(spot_schedule_service) fills them from the sponsorships that want that time
of day, and this module writes them in the block's order.

Every item of an occurrence is queued with the same preempt_at and the block's
timing_mode, in order of position: the same convention the hourly time and
weather entries always used, so playout treats the run as one event.
"""

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, in_category, not_contract_audio, not_holiday_only, not_placeholder
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.spot_schedule_service import (
    SAFETY_MARGIN,
    Break,
    _csv,
    _exists,
    as_utc,
    load_blackouts,
    parse_fixed_times,
    station_tz,
)

logger = logging.getLogger(__name__)

SOURCE = "block"
HORIZON_HOURS = 48  # the same reach as the hourly time and weather it replaces
KINDS = ("asset", "category", "time", "weather", "sponsors")
DEFAULT_DURATION = 30.0


@dataclass
class ResolvedItem:
    kind: str
    label: str
    asset_id: object | None = None
    title: str | None = None
    duration: float | None = None
    problem: str | None = None  # why nothing will play for this item
    optional: bool = False  # the block goes without it when it has nothing to play
    open_place: bool = False  # a sponsor place no sponsorship has taken


@dataclass
class Occurrence:
    at: datetime  # UTC
    local: datetime
    block: ProgramBlock
    items: list[ResolvedItem] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(i.duration or 0 for i in self.items if i.asset_id is not None)

    @property
    def problems(self) -> list[str]:
        return [i.problem for i in self.items if i.problem]


ANNOUNCEMENT_CATEGORIES = ("time_announcement", "weather_spot")


async def drop_double_announcements(db: AsyncSession, station_id, after: datetime) -> int:
    """One time check and one weather per minute. A station that keeps the built-in
    hourly time and weather beside its blocks (blocks_keep_hourly) and has a block
    with its own time or weather item at the same minute queued both: Main at 11:00
    AM on 2026-11-22 said the time and the weather twice. The block's own items stay
    (they are where the block puts them, and the block is not queued again for a
    minute it already has); the built-in copy goes, and the built-in pass leaves that
    minute alone from then on. Pending entries due after `after` only. Does not commit."""
    rows = (
        await db.execute(
            select(QueueEntry.id, QueueEntry.preempt_at, QueueEntry.source, Asset.category)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.source.in_(("auto", SOURCE)),
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > after,
                Asset.category.in_(ANNOUNCEMENT_CATEGORIES),
            )
        )
    ).all()
    from_blocks = {
        (as_utc(at).replace(second=0, microsecond=0), category)
        for _id, at, source, category in rows
        if source == SOURCE
    }
    doubles = [
        entry_id
        for entry_id, at, source, category in rows
        if source == "auto" and (as_utc(at).replace(second=0, microsecond=0), category) in from_blocks
    ]
    if not doubles:
        return 0
    await db.execute(
        delete(QueueEntry)
        .where(QueueEntry.id.in_(doubles))
        .execution_options(synchronize_session=False)
    )
    logger.info(
        "Station %s: %d built-in time/weather entries dropped, the block's own play then",
        station_id, len(doubles),
    )
    return len(doubles)


def blocks_keep_hourly(config: dict | None) -> bool:
    """Station setting `automation_config.blocks_keep_hourly`: its blocks play beside the
    built-in hourly time and weather (a block on the hour plays right after them) instead
    of replacing them. Off by default (blocks replace the hourly time and weather)."""
    return bool((config or {}).get("blocks_keep_hourly"))


def item_label(item: dict) -> str:
    kind = item.get("kind")
    if kind == "time":
        return "Time announcement"
    if kind == "weather":
        return "Weather"
    if kind == "sponsors":
        count = int(item.get("count") or 1)
        return f"{count} sponsor place{'s' if count != 1 else ''}"
    if kind == "category":
        if item.get("category"):
            return f"{item.get('asset_type')} / {item['category']}"
        return str(item.get("asset_type"))
    return "Recording"


async def load_blocks(db: AsyncSession, station_id, active_only: bool = True) -> list[ProgramBlock]:
    stmt = select(ProgramBlock).where(ProgramBlock.station_id == station_id)
    if active_only:
        stmt = stmt.where(ProgramBlock.is_active.is_(True))
    return list((await db.execute(stmt.order_by(ProgramBlock.name))).scalars().all())


# Places a "sponsors" item holds when it only says how long it may run
TIMED_PLACES = 10


def item_places(item: dict) -> int:
    """How many sponsorships a "sponsors" item can hold at most."""
    if item.get("count"):
        return max(0, int(item["count"]))
    return TIMED_PLACES if item.get("max_seconds") else 1


def sponsor_places(block: ProgramBlock) -> int:
    return sum(item_places(i) for i in block.items or [] if i.get("kind") == "sponsors")


def sponsor_seconds(block: ProgramBlock) -> float | None:
    """The most sponsorship time one airing of the block may hold, or None when no
    "sponsors" item sets a limit (then only the places count)."""
    limits = [
        float(i["max_seconds"])
        for i in block.items or []
        if i.get("kind") == "sponsors" and i.get("max_seconds")
    ]
    return sum(limits) if limits else None


async def load_breaks(db: AsyncSession, station: Station, start: datetime, end: datetime):
    """Every airing of the station's blocks that has sponsor places, or None
    when no active block has any (the spot planner then uses clock slots)."""
    blocks = [b for b in await load_blocks(db, station.id) if sponsor_places(b)]
    if not blocks:
        return None
    return [
        Break(
            local=o.local,
            capacity=sponsor_places(o.block),
            timing_mode=o.block.timing_mode,
            name=o.block.name,
            latest_minutes=o.block.latest_minutes,
            seconds=sponsor_seconds(o.block),
        )
        for o in occurrences_between(blocks, station_tz(station.timezone), start, end)
    ]


# Rough length of a time check, weather or one sponsorship when working out where a
# block's shiur starts inside it
SHORT_ITEM_SECONDS = 30.0


async def block_talk_windows(
    db: AsyncSession, station: Station, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """When the shiurim and stories in the station's blocks will be on air (station
    local), from the blocks themselves, queued or not. The queue alone misses a
    block queued in the same pass as the sponsor breaks around it (a fresh fill, a
    new day's Daf Yomi block): the simulator showed Main's 8 PM Daf with
    sponsorships at 8:15, 8:30, 8:45 and 9:00 after PR 394."""
    from app.services.long_items import LONG_FORM_MIN_SECONDS, LONG_FORM_TYPES, is_long_form

    blocks = await load_blocks(db, station.id)
    tz = station_tz(station.timezone)
    assets: dict[str, Asset | None] = {}
    pools: dict[tuple, float] = {}

    async def item_length(item: dict) -> tuple[float, bool]:
        kind = item.get("kind")
        if kind == "asset":
            key = str(item.get("asset_id"))
            if key not in assets:
                try:
                    assets[key] = await db.get(Asset, uuid.UUID(key))
                except (ValueError, TypeError):
                    assets[key] = None
            asset = assets[key]
            if asset is None:
                return 0.0, False
            return float(asset.duration or 0), is_long_form(asset)
        if kind == "category":
            asset_type, category = item.get("asset_type"), item.get("category")
            key = (asset_type, category)
            if key not in pools:
                stmt = select(func.max(Asset.duration)).where(Asset.asset_type == asset_type)
                if category:
                    stmt = stmt.where(in_category(category))
                pools[key] = float((await db.execute(stmt)).scalar() or 0)
            length = pools[key]
            kind_long = (asset_type or "").strip().lower() in LONG_FORM_TYPES
            return length, kind_long or length >= LONG_FORM_MIN_SECONDS
        if kind == "sponsors":
            return float(item.get("max_seconds") or item_places(item) * SHORT_ITEM_SECONDS), False
        return SHORT_ITEM_SECONDS, False

    windows: list[tuple[datetime, datetime]] = []
    for occurrence in occurrences_between(blocks, tz, start, end):
        offset = 0.0
        for item in occurrence.block.items or []:
            length, long_form = await item_length(item)
            if long_form and length > 0:
                begin = occurrence.at + timedelta(seconds=offset)
                windows.append((begin.astimezone(tz), (begin + timedelta(seconds=length)).astimezone(tz)))
            offset += length
    return windows


def hard_by(block: ProgramBlock, at: datetime) -> datetime | None:
    """For a soft block with a limit: when to stop waiting for the song."""
    if block.timing_mode == "soft" and block.latest_minutes:
        return at + timedelta(minutes=block.latest_minutes)
    return None


def in_season(block: ProgramBlock, day: date) -> bool:
    """Is this station-local day inside the block's yearly season? No season = all year.
    "MM-DD" both ends included; a range that wraps the new year works ("09-16" to "05-14")."""
    start, end = getattr(block, "season_start", None), getattr(block, "season_end", None)
    if not start or not end:
        return True
    today = f"{day.month:02d}-{day.day:02d}"
    if start <= end:
        return start <= today <= end
    return today >= start or today <= end


def occurrences_between(
    blocks: list[ProgramBlock], tz, start: datetime, end: datetime, blackouts=None
) -> list[Occurrence]:
    """Every time a block is due in (start, end], oldest first. Items not resolved yet."""
    found: list[Occurrence] = []
    first_day = start.astimezone(tz).date()
    days = (end.astimezone(tz).date() - first_day).days + 1
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        for block in blocks:
            if str(day.weekday()) not in _csv(block.days_of_week):
                continue
            if not in_season(block, day):
                continue
            for hour, minute in parse_fixed_times(block.times):
                local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
                if not _exists(local):
                    continue
                at = local.astimezone(UTC)
                if not (start < at <= end):
                    continue
                if any(b.covers(at) for b in (blackouts or [])):
                    continue
                found.append(Occurrence(at=at, local=local, block=block))
    found.sort(key=lambda o: (o.at, o.block.name or "", str(o.block.id)))
    return found


class _Resolver:
    """Turns a block's items into recordings for one occurrence."""

    def __init__(self, db: AsyncSession, station: Station, dry_run: bool, sponsors=None, prepare: bool = True):
        self.db = db
        # False: time and weather only from recordings that exist (a local FM box's week
        # ahead); nothing is made, so no day gets a report written from today's weather
        self.prepare = prepare
        # (UTC start, block name) -> the sponsorships planned into its places, in order
        self.sponsors: dict[tuple, list] = sponsors or {}
        self.station = station
        self.dry_run = dry_run
        self.assets: dict[str, Asset | None] = {}
        self.pools: dict[tuple, list] = {}
        self.last_used: dict[str, list[datetime]] | None = None
        self.announcements: dict[str, tuple] = {}

    async def _asset(self, asset_id) -> Asset | None:
        key = str(asset_id)
        if key not in self.assets:
            try:
                wanted = uuid.UUID(key)
            except ValueError:
                self.assets[key] = None
            else:
                self.assets[key] = (
                    await self.db.execute(select(Asset).where(Asset.id == wanted))
                ).scalar_one_or_none()
        return self.assets[key]

    async def _rotate(self, asset_type: str, category: str | None, at: datetime):
        """The recording of this type that has gone longest without airing."""
        if self.last_used is None:
            # Every time each recording aired or is queued to air. Only the times
            # before `at` count, so a preview of hours that are already queued
            # shows the same turn-taking as the queue.
            self.last_used = {}
            from app.services.last_played import last_played

            for asset_id, when in (await last_played(self.db, self.station.id)).items():
                self.last_used.setdefault(asset_id, []).append(when)
            queued = await self.db.execute(
                select(QueueEntry.asset_id, QueueEntry.preempt_at).where(
                    QueueEntry.station_id == self.station.id,
                    QueueEntry.status == "pending",
                    QueueEntry.source == SOURCE,
                    QueueEntry.preempt_at.isnot(None),
                )
            )
            for asset_id, when in queued.all():
                self.last_used.setdefault(str(asset_id), []).append(as_utc(when))
        key = (asset_type, category)
        if key not in self.pools:
            stmt = select(Asset.id, Asset.title, Asset.duration).where(
                Asset.asset_type == asset_type, not_placeholder(), not_contract_audio(), not_holiday_only()
            )
            if category:
                stmt = stmt.where(in_category(category))
            else:
                # "any jingle" never means a station ID (those name their category)
                from app.models.asset import not_station_id

                stmt = stmt.where(not_station_id())
            from app.services.station_blocks import blocked_asset_ids

            stmt = stmt.where(Asset.id.notin_(blocked_asset_ids(self.station.id)))
            self.pools[key] = list((await self.db.execute(stmt.order_by(Asset.title))).all())
        pool = self.pools[key]
        if not pool:
            return None
        epoch = datetime.min.replace(tzinfo=UTC)

        def rested_since(row):
            return max((t for t in self.last_used.get(str(row.id), []) if t < at), default=epoch)

        row = min(pool, key=lambda r: (rested_since(r), r.title or ""))
        self.last_used.setdefault(str(row.id), []).append(at)
        return row

    async def _announcement(self, local: datetime):
        slot_key = local.strftime("%Y-%m-%dT%H")
        if not self.prepare and slot_key not in self.announcements:
            from app.services.weather_spot_service import (
                find_time_clip_asset,
                find_weather_spot_asset,
                station_weather_region,
            )

            region = station_weather_region(self.station.automation_config)
            self.announcements[slot_key] = (
                await find_time_clip_asset(self.db, local.hour, region, 30 if local.minute == 30 else 0),
                await find_weather_spot_asset(self.db, local.strftime("%Y-%m-%d"), region),
            )
        if slot_key not in self.announcements:
            try:
                from app.services.weather_spot_service import (
                    get_or_create_weather_spot_assets,
                    station_weather_region,
                )

                self.announcements[slot_key] = await get_or_create_weather_spot_assets(
                    self.db, slot_key, station_weather_region(self.station.automation_config)
                )
            except Exception:
                logger.warning("Time and weather for %s could not be prepared", slot_key)
                self.announcements[slot_key] = (None, None)
        return self.announcements[slot_key]

    async def _sponsor_items(self, planned: list, count: int, timed: bool = False) -> list[ResolvedItem]:
        found = []
        for _ in range(count):
            if not planned:
                if timed:
                    # A time limit, not a number of places: one line says there is room
                    if not found:
                        found.append(
                            ResolvedItem(kind="sponsors", label="Open sponsorship time", open_place=True)
                        )
                    break
                found.append(ResolvedItem(kind="sponsors", label="Open sponsor place", open_place=True))
                continue
            placement = planned.pop(0)
            asset = await self._asset(placement.schedule.asset_id)
            name = placement.schedule.name or (asset.title if asset else "Sponsorship")
            out = ResolvedItem(kind="sponsor", label=f"Sponsor: {name}")
            if asset is None:
                out.problem = f"Sponsor {name}: the spot was removed from the library"
            else:
                out.asset_id, out.title = asset.id, asset.title
                out.duration = float(asset.duration or DEFAULT_DURATION)
            found.append(out)
        return found

    async def resolve(self, occurrence: Occurrence) -> None:
        key = (occurrence.at.replace(second=0, microsecond=0), occurrence.block.name)
        planned = list(self.sponsors.get(key, []))
        for item in occurrence.block.items or []:
            kind = item.get("kind")
            label = item.get("label") or item_label(item)
            if kind == "sponsors":
                occurrence.items.extend(
                    await self._sponsor_items(planned, item_places(item), timed=bool(item.get("max_seconds")))
                )
                continue
            out = ResolvedItem(kind=kind, label=label, optional=bool(item.get("optional")))
            if kind == "asset":
                asset = await self._asset(item.get("asset_id"))
                if asset is None:
                    out.problem = f"{label}: the recording was removed from the library"
                else:
                    out.asset_id, out.title = asset.id, asset.title
                    out.duration = float(asset.duration or DEFAULT_DURATION)
            elif kind == "category":
                row = await self._rotate(item.get("asset_type"), item.get("category"), occurrence.at)
                if row is None:
                    out.problem = f"{label}: nothing of this type is in the library"
                else:
                    out.asset_id, out.title = row.id, row.title
                    out.duration = float(row.duration or DEFAULT_DURATION)
            elif kind in ("time", "weather"):
                if self.dry_run:
                    out.title = "prepared about two days ahead"
                else:
                    time_asset, weather_asset = await self._announcement(occurrence.local)
                    asset = time_asset if kind == "time" else weather_asset
                    if asset is None:
                        out.problem = f"{label}: no recording is ready for this hour"
                    else:
                        out.asset_id, out.title = asset.id, asset.title
                        out.duration = float(asset.duration or DEFAULT_DURATION)
            else:
                out.problem = f"{label}: unknown kind of item"
            occurrence.items.append(out)
        # The block's programme (its last recording) has nothing to play: what comes
        # before it (an announcement of it) is left out too, so listeners never hear
        # "Daf Yomi" announced and then music (Jerusalem 2026-11-17)
        recordings = [i for i in occurrence.items if i.kind in ("asset", "category") and not i.optional]
        if len(recordings) > 1 and recordings[-1].problem:
            for item in recordings[:-1]:
                if not item.problem:
                    item.problem = (f"{item.label}: left out, as {recordings[-1].label} "
                                    "after it has nothing to play")
                    item.asset_id, item.duration = None, None


async def plan_blocks(
    db: AsyncSession,
    station: Station,
    hours: float = HORIZON_HOURS,
    now: datetime | None = None,
    dry_run: bool = True,
    blocks: list[ProgramBlock] | None = None,
    prepare: bool = True,
) -> list[Occurrence]:
    """What the station's blocks will air over the coming hours.

    dry_run leaves time and weather unresolved (preparing them can call the
    voice service); everything else is resolved exactly as it will be queued.
    """
    now = as_utc(now or datetime.now(UTC))
    end = now + timedelta(hours=hours)
    if blocks is None:
        blocks = await load_blocks(db, station.id)
    if not blocks:
        return []
    blackouts = await load_blackouts(db, station.id, now, end)
    found = occurrences_between(blocks, station_tz(station.timezone), now, end, blackouts)
    sponsors: dict[tuple, list] = {}
    if any(sponsor_places(b) for b in blocks):
        from app.services.spot_schedule_service import plan_for_station

        placements, _ = await plan_for_station(
            db, station, days=math.ceil(hours / 24) + 1, now=now
        )
        for p in placements:
            if p.block is not None:
                sponsors.setdefault((p.at, p.block), []).append(p)
    resolver = _Resolver(db, station, dry_run, sponsors, prepare=prepare)
    for occurrence in found:
        await resolver.resolve(occurrence)
    return found


async def reconcile_block_entries(
    db: AsyncSession, station_id, now: datetime | None = None, rebuild: bool = False
) -> dict | None:
    """Queue the occurrences of the station's blocks that are not queued yet.

    Occurrences already in the queue are left as they are, so repeated runs do
    not reshuffle rotating items. `rebuild` (used when a block is edited) drops
    the pending ones first. Returns None when the station has no active block.
    Does not commit.
    """
    now = as_utc(now or datetime.now(UTC))
    earliest = now + SAFETY_MARGIN
    # from the session when it is already there: no round trip per step of a top-up
    station = await db.get(Station, station_id)
    if station is None:
        return None
    blocks = await load_blocks(db, station_id)

    pending = (
        (
            await db.execute(
                select(QueueEntry).where(
                    QueueEntry.station_id == station_id,
                    QueueEntry.status == "pending",
                    QueueEntry.source == SOURCE,
                    QueueEntry.preempt_at.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    removed = 0
    queued_at: set[datetime] = set()
    talks_token = None
    if rebuild and pending:
        from app.services.spot_schedule_service import TALKS_BEFORE_REBUILD, talk_windows

        talks_token = TALKS_BEFORE_REBUILD.set(
            await talk_windows(db, station, station_tz(station.timezone), now)
        )
    try:
        return await _reconcile_blocks(
            db, station, station_id, blocks, pending, rebuild, now, earliest, removed, queued_at
        )
    finally:
        if talks_token is not None:
            TALKS_BEFORE_REBUILD.reset(talks_token)


class _SponsorGuard:
    """Last check before a sponsorship goes into the queue, whatever the plan says:
    never inside a shiur, never the same spot within 20 minutes of itself (queued,
    on air or just played). A plan made at one moment can disagree with one made a
    minute later; on Main 2026-11-18 a new 8:45 occurrence took two sponsorships
    that were still queued at 9:00, during a pause of the Daf Yomi."""

    def __init__(self, talks, near):
        self.talks = talks  # UTC windows
        self.near = near  # asset id -> UTC times

    @classmethod
    async def load(cls, db: AsyncSession, station, now: datetime) -> "_SponsorGuard":
        from app.models.play_log import PlayLog
        from app.services.spot_schedule_service import NEIGHBOUR, TALKS_BEFORE_REBUILD, talk_windows

        talks = TALKS_BEFORE_REBUILD.get()
        if talks is None:
            talks = await talk_windows(db, station, station_tz(station.timezone), now)
        talks = list(talks) + await block_talk_windows(db, station, now - timedelta(hours=2), now + timedelta(days=3))
        near: dict[str, list[datetime]] = {}
        queued = await db.execute(
            select(QueueEntry.asset_id, QueueEntry.preempt_at, QueueEntry.started_at, QueueEntry.status).where(
                QueueEntry.station_id == station.id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
        )
        for asset_id, at, started, status in queued.all():
            when = started if status == "playing" else at
            if when is not None:
                near.setdefault(str(asset_id), []).append(as_utc(when))
        played = await db.execute(
            select(PlayLog.asset_id, PlayLog.start_utc).where(
                PlayLog.station_id == station.id,
                PlayLog.start_utc >= now - NEIGHBOUR,
            )
        )
        for asset_id, start in played.all():
            if start is not None:
                near.setdefault(str(asset_id), []).append(as_utc(start))
        return cls([(as_utc(a), as_utc(b)) for a, b in talks], near)

    def allows(self, asset_id, at: datetime) -> bool:
        from app.services.spot_schedule_service import NEIGHBOUR, TALK_TAIL

        at = as_utc(at)
        if any(start < at < end - TALK_TAIL for start, end in self.talks):
            return False
        return not any(abs(at - t) < NEIGHBOUR for t in self.near.get(str(asset_id), ()))

    def took(self, asset_id, at: datetime) -> None:
        self.near.setdefault(str(asset_id), []).append(as_utc(at))


# A block holding a recording longer than this is a programme (a shiur): no ID after it
_PROGRAMME_SECONDS = 300.0


async def _id_blocks(db: AsyncSession, station, occurrences, earliest: datetime) -> list:
    """The breaks that get the station ID and the ID each gets, for those that have
    none yet: per automation_config.station_ids (on, placement "hourly" = the break at
    hh:00, "every_block" = every break; "between_songs" = none here).
    One per clock time; never after a programme; the rotation is the block's own
    (longest rested first)."""
    from app.services.playlist_generator_service import id_settings_for

    ids = id_settings_for(station.automation_config)
    if not ids.on or ids.placement not in ("hourly", "every_block"):
        return []
    have = {
        as_utc(at).replace(second=0, microsecond=0)
        for (at,) in (await db.execute(
            select(QueueEntry.preempt_at).join(Asset, QueueEntry.asset_id == Asset.id).where(
                QueueEntry.station_id == station.id, QueueEntry.status.in_(["pending", "playing", "played"]),
                QueueEntry.source == SOURCE, QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > earliest - timedelta(hours=1),
                func.lower(Asset.category).like("station_id%"),
            )
        )).all()
    }
    def programme(occurrence) -> bool:
        return any(i.kind in ("asset", "category") and (i.duration or 0) > _PROGRAMME_SECONDS
                   for i in occurrence.items)

    # an ID queued last at a time a programme starts would follow the whole shiur
    programme_at = {o.at.replace(second=0, microsecond=0) for o in occurrences if programme(o)}
    resolver = _Resolver(db, station, False, {}, prepare=False)
    chosen: list = []
    for occurrence in occurrences:
        at = occurrence.at.replace(second=0, microsecond=0)
        if at <= earliest or at in have or at in programme_at:
            continue
        if ids.placement == "hourly" and occurrence.local.minute != 0:
            continue
        if not ids.in_hours(occurrence.local.hour):
            continue
        if not any(i.asset_id is not None for i in occurrence.items):
            continue  # an empty break: no ID on its own
        row = await resolver._rotate("jingle", ids.category, occurrence.at)
        if row is None:
            return chosen
        asset = await db.get(Asset, row.id)
        if asset is None:
            continue
        have.add(at)
        chosen.append((occurrence, asset))
    return chosen


async def _reconcile_blocks(
    db: AsyncSession, station, station_id, blocks, pending, rebuild, now, earliest, removed, queued_at
) -> dict | None:
    for entry in pending:
        at = as_utc(entry.preempt_at).replace(second=0, microsecond=0)
        if (rebuild or not blocks) and at > earliest:
            await db.delete(entry)
            removed += 1
        else:
            queued_at.add(at)
    if not blocks:
        await db.flush()
        return None

    # The built-in hourly time and weather would double up with the blocks: drop
    # what it queued (its timed time and weather entries). Not when the station keeps
    # them beside its blocks (blocks_keep_hourly).
    if not blocks_keep_hourly(station.automation_config):
        legacy = await db.execute(
            select(QueueEntry)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.source == "auto",
                # any timing: since timing_mode exists every one has it, and a purge
                # of only untimed ones left Main's built-in hourly pair queued beside
                # its block's own time and weather (2026-11-22)
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > earliest,
                Asset.category.in_(ANNOUNCEMENT_CATEGORIES),
            )
        )
        for entry in legacy.scalars().all():
            await db.delete(entry)
            removed += 1

    max_pos = (
        await db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0)).where(
                QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"])
            )
        )
    ).scalar() or 0

    added = 0
    skipped: list[str] = []
    guard = await _SponsorGuard.load(db, station, now)
    occurrences = await plan_blocks(db, station, now=now, dry_run=False, blocks=blocks)
    id_blocks = await _id_blocks(db, station, occurrences, earliest)
    for occurrence in occurrences:
        at = occurrence.at.replace(second=0, microsecond=0)
        if at <= earliest or at in queued_at:
            continue
        skipped.extend(occurrence.problems)
        for item in occurrence.items:
            if item.asset_id is None:
                continue
            if item.kind == "sponsor" and not guard.allows(item.asset_id, at):
                continue
            max_pos += 1
            db.add(
                QueueEntry(
                    id=uuid.uuid4(),
                    station_id=station_id,
                    asset_id=item.asset_id,
                    position=max_pos,
                    status="pending",
                    source=SOURCE,
                    preempt_at=at,
                    timing_mode=occurrence.block.timing_mode,
                    hard_by=hard_by(occurrence.block, at),
                )
            )
            added += 1
            if item.kind == "sponsor":
                guard.took(item.asset_id, at)
    # The station ID closes its break (Shmuel 2026-11-19: "part of the ad blocks. Or
    # once an hour"), also in breaks queued before the setting changed
    for occurrence, asset in id_blocks:
        max_pos += 1
        db.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id, position=max_pos,
            status="pending", source=SOURCE, preempt_at=occurrence.at.replace(second=0, microsecond=0),
            timing_mode=occurrence.block.timing_mode, hard_by=hard_by(occurrence.block, occurrence.at),
        ))
        added += 1
    await db.flush()
    removed += await drop_double_announcements(db, station_id, earliest)
    if added or removed:
        logger.info("Program blocks for station %s: +%d -%d", station_id, added, removed)
    return {
        "added": added,
        "removed": removed,
        "problems": sorted(set(skipped)),
        "max_pos": max_pos,
    }
