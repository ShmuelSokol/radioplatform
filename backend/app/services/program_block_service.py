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
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, in_category, not_contract_audio, not_placeholder
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


def hard_by(block: ProgramBlock, at: datetime) -> datetime | None:
    """For a soft block with a limit: when to stop waiting for the song."""
    if block.timing_mode == "soft" and block.latest_minutes:
        return at + timedelta(minutes=block.latest_minutes)
    return None


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

    def __init__(self, db: AsyncSession, station: Station, dry_run: bool, sponsors=None):
        self.db = db
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
                Asset.asset_type == asset_type, not_placeholder(), not_contract_audio()
            )
            if category:
                stmt = stmt.where(in_category(category))
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
            out = ResolvedItem(kind=kind, label=label)
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
        recordings = [i for i in occurrence.items if i.kind in ("asset", "category")]
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
    resolver = _Resolver(db, station, dry_run, sponsors)
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
                QueueEntry.timing_mode.is_(None),
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > earliest,
                Asset.category.in_(("time_announcement", "weather_spot")),
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
    for occurrence in await plan_blocks(
        db, station, now=now, dry_run=False, blocks=blocks
    ):
        at = occurrence.at.replace(second=0, microsecond=0)
        if at <= earliest or at in queued_at:
            continue
        skipped.extend(occurrence.problems)
        for item in occurrence.items:
            if item.asset_id is None:
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
    await db.flush()
    if added or removed:
        logger.info("Program blocks for station %s: +%d -%d", station_id, added, removed)
    return {
        "added": added,
        "removed": removed,
        "problems": sorted(set(skipped)),
        "max_pos": max_pos,
    }
