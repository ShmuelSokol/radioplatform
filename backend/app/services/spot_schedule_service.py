"""
Sponsor spot scheduling.

A SpotSchedule says "air this sponsor's spot N times in each of these dayparts
on these days". This module turns the schedules into concrete clock slots and
keeps the station queue's pending ad_slot entries in step with that plan.

The clock slots themselves are the station's existing ad slot minutes
(automation_config.ad_slot_minutes, default :15/:30/:45). Playback is not
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


@dataclass
class Placement:
    at: datetime  # UTC, minute precision
    local: datetime  # station-local wall time
    daypart: str
    schedule: SpotSchedule


@dataclass
class Unplaced:
    local_date: date
    daypart: str
    schedule: SpotSchedule
    reason: str


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


def _applies(schedule: SpotSchedule, local_date: date, daypart: str) -> bool:
    if not schedule.is_active:
        return False
    if schedule.start_date and local_date < schedule.start_date:
        return False
    if schedule.end_date and local_date > schedule.end_date:
        return False
    if str(local_date.weekday()) not in _csv(schedule.days_of_week):
        return False
    return daypart in _csv(schedule.dayparts)


def plan_spots(
    schedules: list[SpotSchedule],
    tz: ZoneInfo,
    start_date: date,
    days: int,
    slot_minutes: list[int] | None = None,
) -> tuple[list[Placement], list[Unplaced]]:
    """Assign each schedule's plays to clock slots, spread evenly through each daypart.

    Deterministic: the same schedules always produce the same plan for a given
    day, no matter what time it is run, so repeated runs do not shuffle spots.
    When two spots want the same slot, the later one moves to the next free
    slot in that daypart. If a daypart is full, the spot is reported unplaced.
    """
    minutes = sorted({int(m) for m in (slot_minutes or DEFAULT_SLOT_MINUTES) if 0 <= int(m) < 60})
    ordered = sorted(schedules, key=lambda s: (str(s.created_at or ""), str(s.id)))
    placements: list[Placement] = []
    unplaced: list[Unplaced] = []

    for day_offset in range(days):
        local_date = start_date + timedelta(days=day_offset)
        for daypart, (h_start, h_end) in DAYPARTS.items():
            wanting = [s for s in ordered if _applies(s, local_date, daypart)]
            if not wanting:
                continue
            slots = [
                datetime(local_date.year, local_date.month, local_date.day, h, m, tzinfo=tz)
                for h in range(h_start, h_end)
                for m in minutes
            ]
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
                    )
                )

    placements.sort(key=lambda p: p.at)
    return placements, unplaced


async def load_station_schedules(db: AsyncSession, station_id) -> list[SpotSchedule]:
    """All spot schedules (active or not) that apply to this station."""
    result = await db.execute(
        select(SpotSchedule).where(
            or_(SpotSchedule.station_id.is_(None), SpotSchedule.station_id == station_id)
        )
    )
    return list(result.scalars().all())


async def plan_for_station(
    db: AsyncSession, station: Station, days: int = HORIZON_DAYS, now: datetime | None = None
) -> tuple[list[Placement], list[Unplaced]]:
    now = now or datetime.now(UTC)
    schedules = await load_station_schedules(db, station.id)
    tz = station_tz(station.timezone)
    minutes = (station.automation_config or {}).get("ad_slot_minutes") or DEFAULT_SLOT_MINUTES
    return plan_spots(schedules, tz, now.astimezone(tz).date(), days, minutes)


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
    desired: dict[datetime, uuid.UUID] = {
        p.at: p.schedule.asset_id for p in placements if earliest < p.at <= latest
    }

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
