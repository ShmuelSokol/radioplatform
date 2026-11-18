"""What the control room's Sponsor panel shows for one spot on one station.

Opened when an operator taps a sponsorship spot (on air or in the queue): which
sponsor and contract(s) use this recording, the contract's days, times of day and
dates, today's plays on this station, the coming plays in the queue (each one can be
skipped and put back), and the missed plays of the last days.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.spot_schedule_service import (
    SAFETY_MARGIN,
    as_utc,
    clock_12,
    parse_fixed_times,
    station_tz,
)

# An operator's "skip this play": the planner sees the row and does not plan the
# play again, the air watch does not call it a skipped sponsorship, and the
# sponsor's delivery report counts it as missed (it did not air).
CANCELLED = "cancelled"
MISSED_DAYS = 7
UPCOMING_LIMIT = 12
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
STATUS_TEXT = {
    "played": "Aired",
    "playing": "On air",
    "pending": "Waiting",
    "skipped": "Did not air",
    CANCELLED: "Skipped by staff",
}


def days_text(value: str | None) -> str:
    days = sorted({int(d) for d in (value or "").split(",") if d.strip().isdigit() and int(d) < 7})
    if len(days) == 7:
        return "Every day"
    if days == [0, 1, 2, 3, 6]:
        return "Sun-Thu"
    if days == [0, 1, 2, 3, 4, 6]:
        return "Sun-Fri"
    # Sunday first, the way the station's week runs
    return ", ".join(DAY_NAMES[d] for d in sorted(days, key=lambda d: (d + 1) % 7)) or "No days"


def contract_state(schedule: SpotSchedule, today: date) -> str:
    if not schedule.is_active:
        return "paused"
    if schedule.end_date and schedule.end_date < today:
        return "ended"
    if schedule.start_date and schedule.start_date > today:
        return "not_started"
    return "running"


def _local(at: datetime, tz) -> str:
    local = at.astimezone(tz)
    return clock_12(local.hour, local.minute)


async def sponsor_item(
    db: AsyncSession, asset: Asset, station: Station, now: datetime | None = None, show_elsewhere: bool = True
) -> dict:
    now = now or datetime.now(UTC)
    tz = station_tz(station.timezone)
    today = now.astimezone(tz).date()

    schedules = (
        (
            await db.execute(
                select(SpotSchedule)
                .where(SpotSchedule.asset_id == asset.id)
                .order_by(SpotSchedule.created_at)
            )
        )
        .scalars()
        .all()
    )
    # This station's contracts first; others are listed so nothing is hidden
    here = [s for s in schedules if s.station_id is None or str(s.station_id) == str(station.id)]
    elsewhere = [s for s in schedules if s not in here] if show_elsewhere else []
    sponsor_ids = {s.sponsor_id for s in schedules}
    if asset.sponsor_id:
        sponsor_ids.add(asset.sponsor_id)
    sponsors = {}
    if sponsor_ids:
        sponsors = {
            str(sp.id): sp
            for sp in (await db.execute(select(Sponsor).where(Sponsor.id.in_(sponsor_ids)))).scalars().all()
        }
    station_names = {}
    other_ids = {s.station_id for s in elsewhere if s.station_id}
    if other_ids:
        station_names = {
            str(r[0]): r[1]
            for r in (await db.execute(select(Station.id, Station.name).where(Station.id.in_(other_ids)))).all()
        }

    def sponsor_out(sponsor_id) -> dict | None:
        sp = sponsors.get(str(sponsor_id)) if sponsor_id else None
        if sp is None:
            return None
        return {
            "id": str(sp.id),
            "name": sp.name,
            "company_name": sp.company_name,
            "contact_phone": sp.contact_phone,
            "contact_email": sp.contact_email,
        }

    def contract_out(s: SpotSchedule, on_this_station: bool) -> dict:
        fixed = parse_fixed_times(s.fixed_times)
        return {
            "id": str(s.id),
            "name": s.name,
            "sponsor": sponsor_out(s.sponsor_id),
            "station_id": str(s.station_id) if s.station_id else None,
            "station_name": (
                "Every station" if s.station_id is None
                else station.name if on_this_station else station_names.get(str(s.station_id), "Another station")
            ),
            "on_this_station": on_this_station,
            "state": contract_state(s, today),
            "days_of_week": s.days_of_week,
            "days_text": days_text(s.days_of_week),
            "dayparts": [d for d in (s.dayparts or "").split(",") if d.strip()],
            "plays_per_daypart": s.plays_per_daypart,
            "fixed_times": [clock_12(h, m) for h, m in fixed],
            "timing_mode": s.timing_mode,
            "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None,
            "is_active": s.is_active,
        }

    contracts = [contract_out(s, True) for s in here] + [contract_out(s, False) for s in elsewhere]

    # Today on this station: every queue row of this spot due or started today
    midnight = datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(UTC)
    tomorrow = (datetime(today.year, today.month, today.day, tzinfo=tz) + timedelta(days=1)).astimezone(UTC)
    when = func.coalesce(QueueEntry.preempt_at, QueueEntry.started_at)
    rows = (
        (
            await db.execute(
                select(QueueEntry).where(
                    QueueEntry.station_id == station.id,
                    QueueEntry.asset_id == asset.id,
                    or_(
                        (when >= midnight) & (when < tomorrow),
                        QueueEntry.status.in_(["pending", "playing"]),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    def entry_out(e: QueueEntry) -> dict:
        at = as_utc(e.started_at) if e.status in ("played", "playing") and e.started_at else (
            as_utc(e.preempt_at) if e.preempt_at else None
        )
        due = as_utc(e.preempt_at) if e.preempt_at else None
        changeable = (
            e.source == "ad_slot" and due is not None and due > now + SAFETY_MARGIN
        )
        local = at.astimezone(tz) if at else None
        return {
            "entry_id": str(e.id),
            "status": e.status,
            "status_text": STATUS_TEXT.get(e.status, e.status),
            "source": e.source,
            "at": at.isoformat() if at else None,
            "due": due.isoformat() if due else None,
            "local_time": _local(at, tz) if at else None,
            "local_day": (
                "Today" if local and local.date() == today
                else "Tomorrow" if local and local.date() == today + timedelta(days=1)
                else local.strftime("%a %b %-d") if local else None
            ),
            "timing_mode": e.timing_mode,
            "can_skip": changeable and e.status == "pending",
            "can_restore": changeable and e.status == CANCELLED,
        }

    def sort_key(e: QueueEntry):
        at = e.started_at or e.preempt_at
        return as_utc(at) if at else now + timedelta(days=365)

    today_rows = [
        entry_out(e) for e in sorted(rows, key=sort_key)
        if (e.started_at or e.preempt_at) and midnight <= as_utc(e.started_at or e.preempt_at) < tomorrow
    ]
    upcoming = [
        entry_out(e) for e in sorted(rows, key=sort_key)
        if e.status in ("pending", CANCELLED) and e.preempt_at and as_utc(e.preempt_at) >= tomorrow
    ][:UPCOMING_LIMIT]
    # Pending rows without a time (put in by hand) still show as coming
    loose = [entry_out(e) for e in rows if e.status == "pending" and e.preempt_at is None]

    aired_today = (
        await db.execute(
            select(func.count()).select_from(PlayLog).where(
                PlayLog.station_id == station.id,
                PlayLog.asset_id == asset.id,
                PlayLog.start_utc >= midnight,
                PlayLog.start_utc < tomorrow,
            )
        )
    ).scalar() or 0

    # Missed plays: the same report the sponsor sees, for this station and this spot
    missed: list[dict] = []
    if here:
        from app.services.sponsor_delivery import delivery_for_sponsor

        seen = set()
        for s in here:
            sp = sponsors.get(str(s.sponsor_id))
            if sp is None or sp.id in seen:
                continue
            seen.add(sp.id)
            for r in await delivery_for_sponsor(db, sp, now=now, past_days=MISSED_DAYS):
                if r.station_name != station.name or r.asset_title != asset.title:
                    continue
                missed.append({
                    "when": r.when,
                    "local_date": r.local_date.isoformat(),
                    "day": r.local_date.strftime("%a %b %-d"),
                    "daypart": r.daypart,
                    "promised": r.promised,
                    "played": r.played,
                    "missed": r.missed,
                    "moved": r.moved,
                    "note": r.note,
                })

    return {
        "asset": {
            "id": str(asset.id),
            "title": asset.title,
            "artist": asset.artist,
            "asset_type": asset.asset_type,
            "category": asset.category,
            "duration": asset.duration,
        },
        "station": {"id": str(station.id), "name": station.name, "timezone": station.timezone},
        "owner": sponsor_out(asset.sponsor_id),
        "contracts": contracts,
        "aired_today": aired_today,
        "today": today_rows,
        "upcoming": loose + upcoming,
        "missed": missed,
    }
