"""What a sponsor was promised and what aired: the missed plays shown in their portal.

Past: for each of the sponsor's sponsorships, each station day and time of day it
covers, the plays its contract promises against the plays in the play log.
Coming: plays the planner has no room for, and plays moved to a later block.
Times of day the station is off the air (Shabbos, Yom Tov) are not promised.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.spot_schedule_service import (
    DAYPARTS,
    _applies,
    _applies_on,
    as_utc,
    daypart_of,
    load_blackouts,
    parse_fixed_times,
    plan_for_station,
    station_tz,
)

PAST_DAYS = 14
# A soft play waits for the song on air, so one due late in a time of day may
# start a little after it; it still counts for the time of day it was due in.
LATE_START_GRACE = timedelta(minutes=15)
# Narrow time slots before the broad part of the day they sit in, so a play is
# counted for the slot that promised it first.
DAYPART_ORDER = [
    "overnight",
    "early_morning", "late_morning", "morning",
    "early_afternoon", "late_afternoon", "afternoon",
    "early_evening", "late_evening", "evening",
]


@dataclass
class DeliveryRow:
    when: str  # "past" or "coming"
    local_date: date
    daypart: str
    station_name: str
    asset_title: str
    promised: int
    played: int
    missed: int
    moved: int = 0
    note: str = ""


def _promised(schedule: SpotSchedule, local_date: date, daypart: str) -> int:
    """Plays the contract promises in this time of day."""
    fixed = parse_fixed_times(schedule.fixed_times)
    if fixed:
        if not _applies_on(schedule, local_date):
            return 0
        return sum(1 for hour, _ in fixed if daypart_of(hour) == daypart)
    return max(1, schedule.plays_per_daypart or 1) if _applies(schedule, local_date, daypart) else 0


async def delivery_for_sponsor(
    db: AsyncSession, sponsor: Sponsor, now: datetime | None = None, past_days: int = PAST_DAYS
) -> list[DeliveryRow]:
    """Every time of day with a missed or moved play, newest past first, then coming."""
    now = now or datetime.now(UTC)
    schedules = (
        (
            await db.execute(
                select(SpotSchedule).where(
                    SpotSchedule.sponsor_id == sponsor.id, SpotSchedule.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    if not schedules:
        return []
    stations = (
        (await db.execute(select(Station).where(Station.is_active.is_(True)))).scalars().all()
    )
    titles = {
        str(r[0]): r[1]
        for r in (
            await db.execute(
                select(Asset.id, Asset.title).where(Asset.id.in_({s.asset_id for s in schedules}))
            )
        ).all()
    }
    since = now - timedelta(days=past_days + 1)
    plays: dict[tuple[str, str], list[datetime]] = {}
    for station_id, asset_id, start in (
        await db.execute(
            select(PlayLog.station_id, PlayLog.asset_id, PlayLog.start_utc).where(
                PlayLog.asset_id.in_({s.asset_id for s in schedules}), PlayLog.start_utc >= since
            )
        )
    ).all():
        plays.setdefault((str(station_id), str(asset_id)), []).append(as_utc(start))

    past: list[DeliveryRow] = []
    coming: list[DeliveryRow] = []
    for station in stations:
        mine = [s for s in schedules if s.station_id is None or str(s.station_id) == str(station.id)]
        if not mine:
            continue
        tz = station_tz(station.timezone)
        today = now.astimezone(tz).date()
        blackouts = await load_blackouts(db, station.id, since, now)

        for schedule in mine:
            aired = sorted(plays.get((str(station.id), str(schedule.asset_id)), []))
            used: set[int] = set()
            made = as_utc(schedule.created_at) if schedule.created_at else since
            for back in range(past_days, -1, -1):
                local_date = today - timedelta(days=back)
                for daypart in DAYPART_ORDER:
                    start_hour, end_hour = DAYPARTS[daypart]
                    midnight = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
                    start = (midnight + timedelta(hours=start_hour)).astimezone(UTC)
                    end = (midnight + timedelta(hours=end_hour)).astimezone(UTC)
                    # still running, or the contract was entered after it began
                    if end > now or start < made:
                        continue
                    if any(b.start < end and b.end > start for b in blackouts):
                        continue
                    promised = _promised(schedule, local_date, daypart)
                    if not promised:
                        continue
                    played = 0
                    for i, at in enumerate(aired):
                        if i not in used and start <= at < end + LATE_START_GRACE and played < promised:
                            used.add(i)
                            played += 1
                    if played < promised:
                        past.append(
                            DeliveryRow(
                                "past", local_date, daypart, station.name,
                                titles.get(str(schedule.asset_id)) or schedule.name or "Sponsorship",
                                promised, played, promised - played,
                                note="Did not air",
                            )
                        )

        placements, unplaced = await plan_for_station(db, station, now=now)
        ids = {str(s.id) for s in mine}
        slots: dict[tuple[date, str, str], DeliveryRow] = {}

        def row(local_date: date, daypart: str, schedule: SpotSchedule) -> DeliveryRow:
            key = (local_date, daypart, str(schedule.id))
            if key not in slots:
                slots[key] = DeliveryRow(
                    "coming", local_date, daypart, station.name,
                    titles.get(str(schedule.asset_id)) or schedule.name or "Sponsorship",
                    _promised(schedule, local_date, daypart), 0, 0,
                )
            return slots[key]

        for u in unplaced:
            if u.blackout is None and str(u.schedule.id) in ids:
                r = row(u.local_date, u.daypart, u.schedule)
                r.missed += 1
                r.note = "No room left at this time of day"
        for p in placements:
            if p.moved_from and str(p.schedule.id) in ids:
                r = row(p.local.date(), p.moved_from if p.moved_from in DAYPARTS else p.daypart, p.schedule)
                r.moved += 1
                if not r.missed:
                    r.note = f"Moved to a later time the same day ({p.local:%I:%M %p})".replace("(0", "(")
        coming.extend(slots.values())

    order = {d: i for i, d in enumerate(DAYPART_ORDER)}
    past.sort(key=lambda r: (r.local_date, order[r.daypart]), reverse=True)
    coming.sort(key=lambda r: (r.local_date, order[r.daypart]))
    return past + coming
