"""
Sponsor spot scheduling.

A SpotSchedule says "air this sponsor's spot N times in each of these dayparts
on these days". This module turns the schedules into concrete clock slots and
keeps the station queue's pending ad_slot entries in step with that plan.

The clock slots themselves are the station's existing ad slot minutes
(automation_config.ad_slot_minutes, default :15/:30/:45), unless the station
has program blocks with sponsor places: then the spread plays go into those
places instead (see wiki/program-blocks.md) and the block writes them. Playback is not
touched here: entries are written exactly as the legacy ad slot code wrote
them (source="ad_slot" + preempt_at), so the player treats them the same way.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station

logger = logging.getLogger(__name__)

# Station-local hour ranges [start, end)
DAYPARTS: dict[str, tuple[int, int]] = {
    "morning": (6, 12),
    "afternoon": (12, 18),
    "evening": (18, 24),
    "overnight": (0, 6),
}
DEFAULT_SLOT_MINUTES = [15, 30, 45]
HORIZON_DAYS = 7
# Never add/remove a slot this close to air time — the player may be picking it up.
SAFETY_MARGIN = timedelta(minutes=2)


TIMING_MODES = ("hard", "soft")
# Station setting automation_config["spot_timing"] = {"on_the_hour": ..., "other": ...}
DEFAULT_TIMING = {"on_the_hour": "hard", "other": "soft"}


def default_timing(minute: int, station_defaults: dict | None = None) -> str:
    """How a spot in this clock slot honours its time when its schedule does not say.

    hard: the song on air fades out and the spot starts on the second.
    soft: the song finishes and the spot plays right after.
    """
    chosen = {**DEFAULT_TIMING, **(station_defaults or {})}
    mode = chosen["on_the_hour"] if minute == 0 else chosen["other"]
    return mode if mode in TIMING_MODES else DEFAULT_TIMING["other"]


def parse_fixed_times(value: str | None) -> list[tuple[int, int]]:
    """'07:00, 13:30' -> [(7, 0), (13, 30)], sorted, duplicates dropped. Raises ValueError."""
    found = set()
    for part in _csv(value):
        hour, _, minute = part.partition(":")
        h, m = int(hour), int(minute or 0)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"{part} is not a time of day")
        found.add((h, m))
    return sorted(found)


def daypart_of(hour: int) -> str:
    return next(name for name, (start, end) in DAYPARTS.items() if start <= hour < end)


@dataclass
class Placement:
    at: datetime  # UTC, minute precision
    local: datetime  # station-local wall time
    daypart: str
    schedule: SpotSchedule
    timing_mode: str = "soft"
    fixed: bool = False  # an exact time chosen on the schedule, not a spread slot
    block: str | None = None  # name of the program block whose sponsor place this is
    seat: int = 0  # which of that block's places (0 = first)
    hard_by: datetime | None = None  # UTC; soft, but fade the song if still waiting then


@dataclass
class Unplaced:
    local_date: date
    daypart: str
    schedule: SpotSchedule
    reason: str
    blackout: "Blackout | None" = None  # set when the whole daypart is off the air


@dataclass
class Break:
    """One airing of a program block that has sponsor places."""

    local: datetime  # station-local start
    capacity: int  # how many sponsorships fit
    timing_mode: str
    name: str
    latest_minutes: int | None = None  # soft, but no later than this many minutes


@dataclass(frozen=True)
class Blackout:
    """A stretch when the station is off the air (Shabbos, Yom Tov). UTC."""

    start: datetime
    end: datetime
    name: str

    def covers(self, at: datetime) -> bool:
        return self.start <= at < self.end


def station_tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _applies_on(schedule: SpotSchedule, local_date: date) -> bool:
    if not schedule.is_active:
        return False
    if schedule.start_date and local_date < schedule.start_date:
        return False
    if schedule.end_date and local_date > schedule.end_date:
        return False
    return str(local_date.weekday()) in _csv(schedule.days_of_week)


def _applies(schedule: SpotSchedule, local_date: date, daypart: str) -> bool:
    return _applies_on(schedule, local_date) and daypart in _csv(schedule.dayparts)


def _exists(local: datetime) -> bool:
    """False for wall-clock times skipped by a spring-forward clock change
    (e.g. 02:15 on that night). They would land on the next hour's slots in UTC."""
    back = local.astimezone(UTC).astimezone(local.tzinfo)
    return (back.hour, back.minute) == (local.hour, local.minute)


def plan_spots(
    schedules: list[SpotSchedule],
    tz: ZoneInfo,
    start_date: date,
    days: int,
    slot_minutes: list[int] | None = None,
    blackouts: list[Blackout] | None = None,
    timing_defaults: dict | None = None,
    breaks: list[Break] | None = None,
) -> tuple[list[Placement], list[Unplaced]]:
    """Assign each schedule's plays to clock slots, spread evenly through each daypart.

    Deterministic: the same schedules always produce the same plan for a given
    day, no matter what time it is run, so repeated runs do not shuffle spots.
    When two spots want the same slot, the later one moves to the next free
    slot in that daypart. If a daypart is full, the spot is reported unplaced.

    Slots inside a blackout (Shabbos, Yom Tov) are not slots: plays are spread
    over what is left of the daypart, and a daypart that is entirely off the
    air is skipped (reported unplaced with ``blackout`` set).

    ``breaks`` (not None when the station has program blocks with sponsor
    places): spread plays go into those places instead of the clock slots. A
    play aims at the break its share of the daypart points to and takes the
    first free place there, or the next break with room. An exact time that
    is a break's start takes a place in that break.
    """
    blackouts = blackouts or []
    minutes = sorted({int(m) for m in (slot_minutes or DEFAULT_SLOT_MINUTES) if 0 <= int(m) < 60})
    ordered = sorted(schedules, key=lambda s: (str(s.created_at or ""), str(s.id)))
    placements: list[Placement] = []
    unplaced: list[Unplaced] = []

    room: dict[datetime, int] = {}
    by_time: dict[datetime, Break] = {}
    for b in breaks or []:
        if b.capacity > 0 and not any(x.covers(b.local) for x in blackouts):
            by_time[b.local] = b
            room[b.local] = b.capacity

    def seat_in(b: Break, sched) -> Placement:
        seat = b.capacity - room[b.local]
        room[b.local] -= 1
        at = b.local.astimezone(UTC).replace(second=0, microsecond=0)
        return Placement(
            at=at,
            local=b.local,
            daypart=daypart_of(b.local.hour),
            schedule=sched,
            timing_mode=b.timing_mode,
            fixed=bool(getattr(sched, "fixed_times", None)),
            block=b.name,
            seat=seat,
            hard_by=at + timedelta(minutes=b.latest_minutes)
            if b.timing_mode == "soft" and b.latest_minutes
            else None,
        )

    for day_offset in range(days):
        local_date = start_date + timedelta(days=day_offset)

        # Exact times first: they are promises, and the spread plays work around them.
        fixed_at: dict[datetime, SpotSchedule] = {}
        for sched in ordered:
            if not getattr(sched, "fixed_times", None) or not _applies_on(sched, local_date):
                continue
            for hour, minute in parse_fixed_times(sched.fixed_times):
                local = datetime(
                    local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz
                )
                if not _exists(local):
                    continue
                off = next((b for b in blackouts if b.covers(local)), None)
                if off is not None:
                    unplaced.append(
                        Unplaced(
                            local_date,
                            daypart_of(hour),
                            sched,
                            f"station is off the air ({off.name})",
                            blackout=off,
                        )
                    )
                    continue
                if room.get(local):
                    placements.append(seat_in(by_time[local], sched))
                    continue
                if local in fixed_at:
                    other = fixed_at[local].name or "another sponsorship"
                    unplaced.append(
                        Unplaced(
                            local_date,
                            daypart_of(hour),
                            sched,
                            f"{hour:02d}:{minute:02d} is already taken by {other}",
                        )
                    )
                    continue
                fixed_at[local] = sched
                placements.append(
                    Placement(
                        at=local.astimezone(UTC).replace(second=0, microsecond=0),
                        local=local,
                        daypart=daypart_of(hour),
                        schedule=sched,
                        timing_mode=sched.timing_mode
                        if sched.timing_mode in TIMING_MODES
                        else default_timing(minute, timing_defaults),
                        fixed=True,
                    )
                )

        for daypart, (h_start, h_end) in DAYPARTS.items():
            wanting = [
                s
                for s in ordered
                if not getattr(s, "fixed_times", None) and _applies(s, local_date, daypart)
            ]
            if not wanting:
                continue
            if breaks is not None:
                day_breaks = sorted(
                    (
                        b
                        for b in by_time.values()
                        if b.local.date() == local_date and h_start <= b.local.hour < h_end
                    ),
                    key=lambda b: b.local,
                )
                for sched in wanting:
                    plays = max(1, int(sched.plays_per_daypart or 1))
                    for k in range(plays):
                        open_ = [b for b in day_breaks if room[b.local] > 0]
                        if not open_:
                            reason = (
                                "no sponsor place left in this time of day's blocks"
                                if day_breaks
                                else "no block with sponsor places in this time of day"
                            )
                            unplaced.append(Unplaced(local_date, daypart, sched, reason))
                            continue
                        idx = int((k + 0.5) * len(day_breaks) / plays) % len(day_breaks)
                        while room[day_breaks[idx].local] <= 0:
                            idx = (idx + 1) % len(day_breaks)
                        placements.append(seat_in(day_breaks[idx], sched))
                continue
            slots = [
                slot
                for h in range(h_start, h_end)
                for m in minutes
                if _exists(
                    slot := datetime(
                        local_date.year, local_date.month, local_date.day, h, m, tzinfo=tz
                    )
                )
            ]
            off_air = None
            if blackouts and slots:
                first = slots[0]
                on_air = [x for x in slots if not any(b.covers(x) for b in blackouts)]
                if not on_air:
                    off_air = next(b for b in blackouts if b.covers(first))
                slots = on_air
            slots = [x for x in slots if x not in fixed_at]
            if off_air is not None:
                for sched in wanting:
                    for _ in range(max(1, int(sched.plays_per_daypart or 1))):
                        unplaced.append(
                            Unplaced(
                                local_date,
                                daypart,
                                sched,
                                f"station is off the air ({off_air.name})",
                                blackout=off_air,
                            )
                        )
                continue
            taken: dict[int, SpotSchedule] = {}
            for sched in wanting:
                plays = max(1, int(sched.plays_per_daypart or 1))
                for k in range(plays):
                    if not slots or len(taken) >= len(slots):
                        unplaced.append(
                            Unplaced(
                                local_date, daypart, sched, "no free slot left in this daypart"
                            )
                        )
                        continue
                    idx = int((k + 0.5) * len(slots) / plays) % len(slots)
                    while idx in taken:
                        idx = (idx + 1) % len(slots)
                    taken[idx] = sched
            for idx in sorted(taken):
                local = slots[idx]
                placements.append(
                    Placement(
                        at=local.astimezone(UTC).replace(second=0, microsecond=0),
                        local=local,
                        daypart=daypart,
                        schedule=taken[idx],
                        timing_mode=taken[idx].timing_mode
                        if taken[idx].timing_mode in TIMING_MODES
                        else default_timing(local.minute, timing_defaults),
                    )
                )

    placements.sort(key=lambda p: (p.at, p.seat))
    return placements, unplaced


async def load_station_schedules(db: AsyncSession, station_id) -> list[SpotSchedule]:
    """All spot schedules (active or not) that apply to this station."""
    result = await db.execute(
        select(SpotSchedule).where(
            or_(SpotSchedule.station_id.is_(None), SpotSchedule.station_id == station_id)
        )
    )
    return list(result.scalars().all())


async def load_blackouts(
    db: AsyncSession, station_id, start: datetime, end: datetime
) -> list[Blackout]:
    """Blackout windows touching [start, end) that apply to this station."""
    result = await db.execute(
        select(HolidayWindow).where(
            HolidayWindow.is_blackout.is_(True),
            HolidayWindow.start_datetime < end,
            HolidayWindow.end_datetime > start,
        )
    )
    found = []
    for w in result.scalars().all():
        if w.affected_stations is not None:
            ids = [str(x) for x in (w.affected_stations.get("station_ids") or [])]
            if str(station_id) not in ids:
                continue
        found.append(Blackout(as_utc(w.start_datetime), as_utc(w.end_datetime), w.name))
    return sorted(found, key=lambda b: b.start)


async def plan_for_station(
    db: AsyncSession, station: Station, days: int = HORIZON_DAYS, now: datetime | None = None
) -> tuple[list[Placement], list[Unplaced]]:
    now = now or datetime.now(UTC)
    schedules = await load_station_schedules(db, station.id)
    tz = station_tz(station.timezone)
    minutes = (station.automation_config or {}).get("ad_slot_minutes") or DEFAULT_SLOT_MINUTES
    blackouts = await load_blackouts(
        db, station.id, now - timedelta(days=1), now + timedelta(days=days + 1)
    )
    from app.services.program_block_service import load_breaks

    breaks = await load_breaks(
        db, station, now - timedelta(days=1), now + timedelta(days=days + 1)
    )
    return plan_spots(
        schedules,
        tz,
        now.astimezone(tz).date(),
        days,
        minutes,
        blackouts,
        (station.automation_config or {}).get("spot_timing"),
        breaks,
    )


async def reconcile_spot_slots(
    db: AsyncSession, station_id, now: datetime | None = None
) -> dict | None:
    """Make the station's pending ad_slot queue entries match the spot schedules.

    Returns None when the station has no spot schedules at all — the caller
    then keeps the legacy single-spot behaviour. Otherwise returns counts.
    Does not commit.
    """
    schedules = await load_station_schedules(db, station_id)
    if not schedules:
        return None

    station = (
        await db.execute(select(Station).where(Station.id == station_id))
    ).scalar_one_or_none()
    if station is None:
        return None

    now = now or datetime.now(UTC)
    earliest = now + SAFETY_MARGIN
    latest = now + timedelta(days=HORIZON_DAYS)

    placements, unplaced = await plan_for_station(db, station, days=HORIZON_DAYS + 1, now=now)
    # Places inside program blocks are written by the block, in its order.
    desired: dict[datetime, uuid.UUID] = {
        p.at: p.schedule.asset_id
        for p in placements
        if earliest < p.at <= latest and p.block is None
    }
    timing: dict[datetime, str] = {p.at: p.timing_mode for p in placements}

    existing = await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == "ad_slot",
            QueueEntry.preempt_at.isnot(None),
        )
    )
    kept: set[datetime] = set()
    removed = 0
    for entry in existing.scalars().all():
        at = as_utc(entry.preempt_at).replace(second=0, microsecond=0)
        if at <= earliest:
            continue  # about to air (or overdue) — leave it alone
        if at not in kept and str(desired.get(at)) == str(entry.asset_id):
            kept.add(at)
            if entry.timing_mode != timing[at]:
                entry.timing_mode = timing[at]
        else:
            await db.delete(entry)
            removed += 1

    max_pos = (
        await db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0)).where(
                QueueEntry.station_id == station_id, QueueEntry.status == "pending"
            )
        )
    ).scalar() or 0

    added = 0
    for at in sorted(desired):
        if at in kept:
            continue
        max_pos += 1
        db.add(
            QueueEntry(
                id=uuid.uuid4(),
                station_id=station_id,
                asset_id=desired[at],
                position=max_pos,
                status="pending",
                source="ad_slot",
                preempt_at=at,
                timing_mode=timing[at],
            )
        )
        added += 1

    await db.flush()
    if added or removed:
        logger.info(
            "Spot slots reconciled for station %s: +%d -%d (kept %d, unplaced %d)",
            station_id,
            added,
            removed,
            len(kept),
            len(unplaced),
        )
    return {
        "added": added,
        "removed": removed,
        "kept": len(kept),
        "unplaced": len(unplaced),
        "max_pos": max_pos,
    }
