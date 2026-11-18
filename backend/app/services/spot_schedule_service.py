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
# The four broad parts of the day (older contracts, reports and daypart_of).
BROAD_DAYPARTS: dict[str, tuple[int, int]] = {
    "morning": (6, 12),
    "afternoon": (12, 18),
    "evening": (18, 24),
    "overnight": (0, 6),
}
# Three-hour time slots a contract is sold into (Shmuel 2026-11-18: "a zone of a
# couple hours", not an exact minute; the planner levels the breaks inside it).
TIME_SLOTS: dict[str, tuple[int, int]] = {
    "early_morning": (6, 9),
    "late_morning": (9, 12),
    "early_afternoon": (12, 15),
    "late_afternoon": (15, 18),
    "early_evening": (18, 21),
    "late_evening": (21, 24),
}
# Every name a contract may use. Planned narrowest first, so a contract held to
# 6-9 AM is seated before one that may go anywhere in the morning.
DAYPARTS: dict[str, tuple[int, int]] = {
    "overnight": (0, 6),
    **TIME_SLOTS,
    "morning": (6, 12),
    "afternoon": (12, 18),
    "evening": (18, 24),
}
DEFAULT_SLOT_MINUTES = [15, 30, 45]
# Length counted for a sponsorship whose recording has no measured length
DEFAULT_SPOT_SECONDS = 30.0
HORIZON_DAYS = 7
# Never add/remove a slot this close to air time — the player may be picking it up.
SAFETY_MARGIN = timedelta(minutes=2)


TIMING_MODES = ("hard", "soft")
# Station setting automation_config["spot_timing"] =
#   {"on_the_hour": ..., "half_hour": ..., "other": ...}
# half_hour (Shmuel 2026-11-18: the :30 time and weather on the second, so listeners
# know when to tune in) falls back to "other" when a station has not chosen it.
DEFAULT_TIMING = {"on_the_hour": "hard", "other": "soft"}
TIMING_KEYS = ("on_the_hour", "half_hour", "other")


def timing_choices(station_defaults: dict | None = None) -> dict:
    """The station's three choices, with half_hour shown as "other" until it is set."""
    saved = {k: v for k, v in (station_defaults or {}).items() if k in TIMING_KEYS and v in TIMING_MODES}
    chosen = {**DEFAULT_TIMING, **saved}
    chosen.setdefault("half_hour", chosen["other"])
    return {k: chosen[k] for k in TIMING_KEYS}


def default_timing(minute: int, station_defaults: dict | None = None) -> str:
    """How a spot in this clock slot honours its time when its schedule does not say.

    hard: the song on air fades out and the spot starts on the second.
    soft: the song finishes and the spot plays right after.
    """
    chosen = timing_choices(station_defaults)
    if minute == 0:
        return chosen["on_the_hour"]
    if minute == 30:
        return chosen["half_hour"]
    return chosen["other"]


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
    """The broad part of the day (morning/afternoon/evening/overnight) an hour is in."""
    return next(name for name, (start, end) in BROAD_DAYPARTS.items() if start <= hour < end)


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
    # Its own time of day had no room, so it rolled to a later block the same day
    moved_from: str | None = None


@dataclass
class Unplaced:
    local_date: date
    daypart: str
    schedule: SpotSchedule
    reason: str
    blackout: "Blackout | None" = None  # set when the whole daypart is off the air


def clock_12(hour: int, minute: int) -> str:
    """6:15 PM, the way the rest of the site writes a time."""
    return f"{(hour % 12) or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def daypart_over(u: "Unplaced", tz: ZoneInfo, now: datetime) -> bool:
    """True when the play's time of day has already ended (nothing to fix there)."""
    end_hour = DAYPARTS[u.daypart][1]
    end = datetime(u.local_date.year, u.local_date.month, u.local_date.day, tzinfo=tz) + timedelta(
        hours=end_hour
    )
    return end <= now


@dataclass
class Break:
    """One airing of a program block that has sponsor places."""

    local: datetime  # station-local start
    capacity: int  # how many sponsorships fit
    timing_mode: str
    name: str
    latest_minutes: int | None = None  # soft, but no later than this many minutes
    # The most sponsorship time one airing may hold (None = only the places count)
    seconds: float | None = None


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
    durations: dict[str, float] | None = None,
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
    places): spread plays go into those places instead of the clock slots. Each
    play goes to the break of its time slot that holds the fewest sponsorships
    so far (Shmuel 2026-11-18: every break gets 3 before any gets a 4th), never
    beside its own contract when another break is as empty; ties go to the
    break its share of the slot points to, so a contract's plays stay apart. An exact time that
    is a break's start takes a place in that break.

    A break may also limit its sponsorship time (``Break.seconds``); a play then
    needs a free place and enough time left for its length (``durations``: asset
    id -> seconds, DEFAULT_SPOT_SECONDS when unknown).

    More plays than a time of day's blocks hold (see ``_fair_order``): everyone
    gets one play before anyone gets a second, contracts about to end go first,
    and the rest take turns by day, so the same sponsorship does not lose out
    every day. Plays still without room roll to a later block the same day that
    has room left after its own time of day is placed (``Placement.moved_from``).
    Only when the whole rest of the day is full is a play reported unplaced.
    With enough room, nothing changes from the simple first-booked order.
    """
    blackouts = blackouts or []
    minutes = sorted({int(m) for m in (slot_minutes or DEFAULT_SLOT_MINUTES) if 0 <= int(m) < 60})
    ordered = sorted(schedules, key=lambda s: (str(s.created_at or ""), str(s.id)))
    placements: list[Placement] = []
    unplaced: list[Unplaced] = []

    room: dict[datetime, int] = {}
    time_left: dict[datetime, float] = {}
    by_time: dict[datetime, Break] = {}
    for b in breaks or []:
        if b.capacity > 0 and not any(x.covers(b.local) for x in blackouts):
            by_time[b.local] = b
            room[b.local] = b.capacity
            if b.seconds is not None:
                time_left[b.local] = float(b.seconds)

    def length(sched) -> float:
        return float((durations or {}).get(str(sched.asset_id)) or DEFAULT_SPOT_SECONDS)

    def fits(b: Break, sched) -> bool:
        if room.get(b.local, 0) <= 0:
            return False
        return b.local not in time_left or time_left[b.local] + 0.5 >= length(sched)

    def seat_in(b: Break, sched, daypart: str | None = None) -> Placement:
        seat = b.capacity - room[b.local]
        room[b.local] -= 1
        if b.local in time_left:
            time_left[b.local] -= length(sched)
        at = b.local.astimezone(UTC).replace(second=0, microsecond=0)
        return Placement(
            at=at,
            local=b.local,
            daypart=daypart or daypart_of(b.local.hour),  # the contract's own slot when spread
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
                if local in by_time and fits(by_time[local], sched):
                    placements.append(seat_in(by_time[local], sched))
                    continue
                if local in fixed_at:
                    other = fixed_at[local].name or "another sponsorship"
                    unplaced.append(
                        Unplaced(
                            local_date,
                            daypart_of(hour),
                            sched,
                            f"{clock_12(hour, minute)} is already taken by {other}",
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

        waiting: list[Unplaced] = []  # block plays with no room in their time of day
        holding: dict[datetime, set] = {}  # break -> contracts seated in it today
        day_taken: dict[datetime, SpotSchedule] = {}  # clock slots taken today
        for daypart, (h_start, h_end) in DAYPARTS.items():
            wanting = [
                s
                for s in ordered
                if not getattr(s, "fixed_times", None) and _applies(s, local_date, daypart)
            ]
            if not wanting:
                continue
            if breaks is not None:
                # A part of the day wholly inside Shabbos/Yom Tov is skipped on
                # purpose (reported with ``blackout`` set, like the slot path).
                points = [
                    x
                    for h in range(h_start, h_end)
                    for m in (0, 15, 30, 45)
                    if _exists(
                        x := datetime(
                            local_date.year, local_date.month, local_date.day, h, m, tzinfo=tz
                        )
                    )
                ]
                off = None
                if blackouts and points and all(
                    any(b.covers(x) for b in blackouts) for x in points
                ):
                    off = next(b for b in blackouts if b.covers(points[0]))
                if off is not None:
                    for sched in wanting:
                        for _ in range(max(1, int(sched.plays_per_daypart or 1))):
                            unplaced.append(
                                Unplaced(
                                    local_date,
                                    daypart,
                                    sched,
                                    f"station is off the air ({off.name})",
                                    blackout=off,
                                )
                            )
                    continue
                day_breaks = sorted(
                    (
                        b
                        for b in by_time.values()
                        if b.local.date() == local_date and h_start <= b.local.hour < h_end
                    ),
                    key=lambda b: b.local,
                )
                def place(order: list[tuple], commit: bool) -> tuple[list, list]:
                    """Seat the plays in this order; without `commit` the room is put back."""
                    saved = (dict(room), dict(time_left), {k: set(v) for k, v in holding.items()})
                    seated, left_out = [], []
                    for sched, k, plays in order:
                        if not day_breaks:
                            left_out.append((sched, "no block with sponsor places in this time of day"))
                            continue
                        idx = int((k + 0.5) * len(day_breaks) / plays) % len(day_breaks)
                        n = len(day_breaks)
                        open_ = [(i, b) for i, b in enumerate(day_breaks) if fits(b, sched)]
                        chosen = min(
                            open_,
                            key=lambda ib: (
                                ib[1].capacity - room[ib[1].local],  # sponsorships already there
                                id(sched) in holding.get(ib[1].local, ()),
                                (ib[0] - idx) % n,  # nearest after its aim
                            ),
                            default=(None, None),
                        )[1]
                        if chosen is None:
                            left_out.append((sched, "no sponsor place left in this time of day's blocks"))
                            continue
                        seated.append(seat_in(chosen, sched, daypart))
                        holding.setdefault(chosen.local, set()).add(id(sched))
                    if not commit:
                        room.clear()
                        room.update(saved[0])
                        time_left.clear()
                        time_left.update(saved[1])
                        holding.clear()
                        holding.update({k: set(v) for k, v in saved[2].items()})
                    return seated, left_out

                booked = [
                    (sched, k, plays)
                    for sched in wanting
                    for plays in [max(1, int(sched.plays_per_daypart or 1))]
                    for k in range(plays)
                ]
                _, left_out = place(booked, commit=False)
                if left_out and day_breaks:
                    # Over-booked: share the room fairly instead of first booked, first served
                    booked = _fair_order(wanting, local_date, list(DAYPARTS).index(daypart))
                seated, left_out = place(booked, commit=True)
                placements.extend(seated)
                for sched, reason in left_out:
                    waiting.append(Unplaced(local_date, daypart, sched, reason))
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
            # A slot taken by a narrower time slot planned earlier today is not free.
            taken: dict[int, SpotSchedule] = {
                i: day_taken[x] for i, x in enumerate(slots) if x in day_taken
            }
            already = set(taken)
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
                    day_taken[slots[idx]] = sched
            for idx in already:
                del taken[idx]
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

        # Roll what found no room to a later block the same day. Every time of day
        # has placed its own plays by now, so a rolled play never takes their room.
        for u in waiting:
            later = sorted(
                (
                    b
                    for b in by_time.values()
                    if b.local.date() == local_date and b.local.hour >= DAYPARTS[u.daypart][1]
                ),
                key=lambda b: b.local,
            )
            chosen = next((b for b in later if fits(b, u.schedule)), None)
            if chosen is None or "no block" in u.reason:
                unplaced.append(u)
                continue
            moved = seat_in(chosen, u.schedule)
            moved.moved_from = u.daypart
            placements.append(moved)

    placements.sort(key=lambda p: (p.at, p.seat))
    return placements, unplaced


# A contract with this many days or fewer left goes first when room is short
URGENT_DAYS = 3


def _fair_order(wanting: list[SpotSchedule], local_date: date, turn: int) -> list[tuple]:
    """The order plays are seated in when a time of day is over-booked.

    Round by round (everyone's first play before anyone's second). Within a
    round, contracts ending within URGENT_DAYS go first: they cannot make the
    play up later. The others take turns: the starting point moves on by one
    each day and each time of day, so the play that loses out is not always the
    same sponsor's. Depends only on the date, never on when the plan is made,
    so repeated runs give the same plan.
    """

    def urgent(s) -> bool:
        return bool(s.end_date) and (s.end_date - local_date).days <= URGENT_DAYS

    first = sorted((s for s in wanting if urgent(s)), key=lambda s: (s.end_date, str(s.created_at or ""), str(s.id)))
    rest = [s for s in wanting if not urgent(s)]
    if rest:
        start = (local_date.toordinal() + turn) % len(rest)
        rest = rest[start:] + rest[:start]
    rounds = max(max(1, int(s.plays_per_daypart or 1)) for s in wanting)
    order = []
    for k in range(rounds):
        for s in first + rest:
            plays = max(1, int(s.plays_per_daypart or 1))
            if k < plays:
                order.append((s, k, plays))
    return order


async def load_station_schedules(db: AsyncSession, station_id) -> list[SpotSchedule]:
    """All spot schedules (active or not) that apply to this station."""
    result = await db.execute(
        select(SpotSchedule).where(
            or_(SpotSchedule.station_id.is_(None), SpotSchedule.station_id == station_id)
        )
    )
    return list(result.scalars().all())


async def airable_schedules(db: AsyncSession, schedules: list[SpotSchedule]) -> list[SpotSchedule]:
    """The schedules whose recording an automatic pick may use. One marked do-not-play
    (one tap in the control room), a placeholder or one rejected in review is not
    planned: the queue refill removes such entries (_purge_placeholders), so planning
    them made and deleted every slot on every queue read, about 12 dead queue rows a
    second (2026-11-18). It is planned again as soon as the mark is taken off."""
    if not schedules:
        return schedules
    from app.models.asset import Asset, not_placeholder

    ok = set((await db.execute(
        select(Asset.id).where(Asset.id.in_({s.asset_id for s in schedules}), not_placeholder())
    )).scalars().all())
    return [s for s in schedules if s.asset_id in ok]


async def lapsed_contract_assets(
    db: AsyncSession, station: Station, now: datetime | None = None
) -> set[str]:
    """Sponsorship audio whose contract does not cover today on this station.

    An asset some spot schedule uses is contract audio: it airs only while a
    schedule for this station (or every station) is active and today, in station
    time, is inside its dates and days. A queued sponsorship that is still waiting
    (a soft slot behind a long shiur, a station that was stopped) must not air on
    a day its contract no longer covers: a contract ending today used to air at
    00:05 tomorrow from a 23:45 slot. Ids are strings."""
    now = now or datetime.now(UTC)
    today = now.astimezone(station_tz(station.timezone)).date()
    rows = (await db.execute(select(SpotSchedule))).scalars().all()
    used: set[str] = set()
    covered: set[str] = set()
    for s in rows:
        used.add(str(s.asset_id))
        if s.station_id is not None and str(s.station_id) != str(station.id):
            continue
        if _applies_on(s, today):
            covered.add(str(s.asset_id))
    return used - covered


async def skip_lapsed_sponsorships(db: AsyncSession, station_id, now: datetime | None = None) -> int:
    """Mark pending automatic queue entries of lapsed contract audio that are due
    (or due within the safety margin) as skipped (see lapsed_contract_assets). Later
    ones are removed by reconcile_spot_slots or the block rebuild. An operator's own
    pick is left alone."""
    from sqlalchemy import update

    from app.api.v1.queue import OPERATOR_SOURCES

    # from the session when it is already there: no round trip per step of a top-up
    station = await db.get(Station, station_id)
    if station is None:
        return 0
    now = now or datetime.now(UTC)
    lapsed = await lapsed_contract_assets(db, station, now)
    if not lapsed:
        return 0
    ids = [uuid.UUID(x) for x in lapsed]
    result = await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.asset_id.in_(ids),
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now + SAFETY_MARGIN),
            or_(QueueEntry.source.is_(None), QueueEntry.source.notin_(OPERATOR_SOURCES)),
        )
        .values(status="skipped")
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        logger.info("Station %s: %d queued sponsorship(s) skipped, contract over", station_id, result.rowcount)
    return result.rowcount or 0


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
    from app.services.station_modes import spots_on

    if not spots_on(station):
        # Spots mode off (control room): no sponsorship goes in the queue
        return [], []
    schedules = await airable_schedules(db, await load_station_schedules(db, station.id))
    tz = station_tz(station.timezone)
    minutes = (station.automation_config or {}).get("ad_slot_minutes") or DEFAULT_SLOT_MINUTES
    blackouts = await load_blackouts(
        db, station.id, now - timedelta(days=1), now + timedelta(days=days + 1)
    )
    from app.services.program_block_service import load_breaks

    breaks = await load_breaks(
        db, station, now - timedelta(days=1), now + timedelta(days=days + 1)
    )
    durations: dict[str, float] = {}
    if breaks and any(b.seconds is not None for b in breaks) and schedules:
        from app.models.asset import Asset

        rows = await db.execute(
            select(Asset.id, Asset.duration).where(Asset.id.in_({s.asset_id for s in schedules}))
        )
        durations = {str(r[0]): float(r[1]) for r in rows.all() if r[1]}
    placements, unplaced = plan_spots(
        schedules,
        tz,
        now.astimezone(tz).date(),
        days,
        minutes,
        blackouts,
        (station.automation_config or {}).get("spot_timing"),
        breaks,
        durations,
    )
    # A part of the day that is already over cannot be fixed; leave it out.
    unplaced = [u for u in unplaced if not daypart_over(u, tz, now)]
    return placements, unplaced


async def reconcile_spot_slots(
    db: AsyncSession, station_id, now: datetime | None = None
) -> dict | None:
    """Make the station's pending ad_slot queue entries match the spot schedules.

    Returns None when the station has no spot schedules at all (and Spots mode is
    on) — the caller then keeps the legacy single-spot behaviour. Otherwise returns counts.
    Does not commit.
    """
    from app.services.station_modes import spots_on

    # from the session when it is already there: no round trip per step of a top-up
    station = await db.get(Station, station_id)
    if station is None:
        return None
    # Spots mode off: carry on with an empty plan even with no schedules, so the
    # placeholder ad slots of the legacy path are removed and none are added.
    if spots_on(station) and not await load_station_schedules(db, station_id):
        return None

    now = now or datetime.now(UTC)
    earliest = now + SAFETY_MARGIN
    latest = now + timedelta(days=HORIZON_DAYS)
    # Overdue or about-to-air slots are left alone below; one whose contract no
    # longer covers today must still never air.
    await skip_lapsed_sponsorships(db, station_id, now)

    placements, unplaced = await plan_for_station(db, station, days=HORIZON_DAYS + 1, now=now)
    # Places inside program blocks are written by the block, in its order.
    desired: dict[datetime, uuid.UUID] = {
        p.at: p.schedule.asset_id
        for p in placements
        if now < p.at <= latest and p.block is None
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
    tz = station_tz(station.timezone)

    def part(at: datetime):
        local = at.astimezone(tz)
        return local.date(), daypart_of(local.hour)

    for entry in sorted(existing.scalars().all(), key=lambda e: as_utc(e.preempt_at)):
        at = as_utc(entry.preempt_at).replace(second=0, microsecond=0)
        if at <= earliest:
            # About to air (or overdue): left alone. When an edit moved the plan away
            # from it, it still airs, so it stands in for the contract's next planned
            # play in that part of the day; the plan added that one on top and the
            # sponsorship aired once more than its contract says.
            if at > now.replace(second=0, microsecond=0) and str(desired.get(at)) != str(entry.asset_id):
                later = sorted(
                    t for t, wanted in desired.items()
                    if t > at and str(wanted) == str(entry.asset_id) and part(t) == part(at)
                )
                if later:
                    del desired[later[0]]
            continue
        if at not in kept and str(desired.get(at)) == str(entry.asset_id):
            kept.add(at)
            if entry.timing_mode != timing[at]:
                entry.timing_mode = timing[at]
        else:
            await db.delete(entry)
            removed += 1

    # Plays an operator skipped (Sponsor panel) stay skipped: not planned again
    cancelled = await db.execute(
        select(QueueEntry.preempt_at, QueueEntry.asset_id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "cancelled",
            QueueEntry.source == "ad_slot",
            QueueEntry.preempt_at > now,
        )
    )
    for at_raw, asset_id in cancelled.all():
        at = as_utc(at_raw).replace(second=0, microsecond=0)
        if str(desired.get(at)) == str(asset_id):
            kept.add(at)

    max_pos = (
        await db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0)).where(
                QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"])
            )
        )
    ).scalar() or 0

    # A spot due within the safety margin is still added (so an exact time set
    # a minute before it is due airs), but only once: skip it when any ad_slot
    # entry for that minute exists, whatever its status (it may have aired).
    soon = [at for at in desired if at <= earliest]
    if soon:
        rows = await db.execute(
            select(QueueEntry.preempt_at).where(
                QueueEntry.station_id == station_id,
                QueueEntry.source == "ad_slot",
                QueueEntry.preempt_at >= min(soon),
                QueueEntry.preempt_at <= max(soon) + timedelta(minutes=1),
            )
        )
        kept |= {as_utc(r[0]).replace(second=0, microsecond=0) for r in rows.all()}

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


@dataclass
class Shortfall:
    """How one sponsorship fares in a plan: plays promised, missed and moved."""

    schedule: SpotSchedule
    promised: int = 0
    missed: int = 0
    moved: int = 0

    def ends_within(self, today: date, days: int) -> bool:
        end = self.schedule.end_date
        return bool(end) and (end - today).days <= days


def shortfalls(placements: list[Placement], unplaced: list[Unplaced]) -> dict[str, Shortfall]:
    """Per sponsorship: its plays in the plan, and how many found no room or were
    moved. Plays set aside for Shabbos / Yom Tov are not promised plays."""
    found: dict[str, Shortfall] = {}
    for p in placements:
        entry = found.setdefault(str(p.schedule.id), Shortfall(p.schedule))
        entry.promised += 1
        entry.moved += 1 if p.moved_from else 0
    for u in unplaced:
        if u.blackout is not None:
            continue
        entry = found.setdefault(str(u.schedule.id), Shortfall(u.schedule))
        entry.promised += 1
        entry.missed += 1
    return found


async def room_warnings(
    db: AsyncSession, station: Station, schedule_id=None, days: int = HORIZON_DAYS, now: datetime | None = None
) -> list[str]:
    """Plain lines for whoever just saved a sponsorship: what does not fit on this
    station in the coming days. The saved sponsorship (`schedule_id`) comes first;
    others that now lose plays because of it are named too."""
    now = now or datetime.now(UTC)
    placements, unplaced = await plan_for_station(db, station, days=days, now=now)
    found = shortfalls(placements, unplaced)
    short = [f for f in found.values() if f.missed]
    moved = found.get(str(schedule_id)) if schedule_id else None
    lines: list[str] = []
    if not short and not (moved and moved.moved):
        return lines
    from app.models.sponsor import Sponsor

    rows = await db.execute(
        select(Sponsor.id, Sponsor.name).where(Sponsor.id.in_({f.schedule.sponsor_id for f in short}))
    ) if short else None
    names = {str(r[0]): r[1] for r in rows.all()} if rows is not None else {}
    today = now.astimezone(station_tz(station.timezone)).date()
    short.sort(key=lambda f: (str(f.schedule.id) != str(schedule_id), -f.missed))
    for f in short:
        mine = str(f.schedule.id) == str(schedule_id)
        sponsor = names.get(str(f.schedule.sponsor_id))
        # Two schedules of one sponsor read alike without the schedule's own name
        named = f"{sponsor} ({f.schedule.name})" if sponsor and f.schedule.name else sponsor or f.schedule.name
        who = "This sponsorship" if mine else (named or "A sponsorship")
        line = (
            f"{station.name}: {who} misses {f.missed} of {f.promised} plays in the next {days} days "
            "(no room left in the blocks)."
        )
        if f.ends_within(today, days):
            line += f" Its contract ends {f.schedule.end_date:%b} {f.schedule.end_date.day}, so they cannot be made up."
        lines.append(line)
    if moved and moved.moved:
        lines.append(
            f"{station.name}: {moved.moved} of this sponsorship's plays move to a later block the same day."
        )
    if short:
        lines.append("Add sponsorship room to a block, add a block, or lower the plays. See Schedule Check.")
    return lines
