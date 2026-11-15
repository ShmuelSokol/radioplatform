"""
Schedule look-ahead: what will air over the coming days, where schedule blocks
collide, how each collision is settled, and what still needs a person.

It walks the coming days in 15-minute steps of station time and asks the same
resolver the playout engine uses (SchedulingService.rank_matching_blocks), so
what this reports is what the engine will do.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.station import Station
from app.services.scheduling import SchedulingService
from app.services.spot_schedule_service import load_blackouts, plan_for_station, station_tz

STEP = timedelta(minutes=15)

# kinds
OVERLAP = "overlap"  # two or more blocks cover the same time; one wins
GAP = "gap"  # no block covers this time
SPOT_UNPLACED = "spot_unplaced"  # a sponsor play has no free slot
OFF_AIR = "off_air"  # Shabbos / Yom Tov


@dataclass
class Finding:
    kind: str
    start: datetime  # UTC
    end: datetime  # UTC
    title: str
    detail: str
    resolution: str  # what the system does about it
    needs_attention: bool
    blocks: list[dict] = field(default_factory=list)


def _why(winner, loser) -> str:
    """Plain-language reason the winner beats the loser. Each arg: (schedule, block)."""
    (ws, wb), (ls, lb) = winner, loser
    if (ws.priority or 0) != (ls.priority or 0):
        return f'schedule "{ws.name}" has higher priority than "{ls.name}"'
    if (wb.priority or 0) != (lb.priority or 0):
        return f"it has higher priority ({wb.priority or 0} over {lb.priority or 0})"
    if wb.recurrence_type != lb.recurrence_type:
        return "equal priority, so the more specific block wins"
    return "equal priority, so the shorter (then newer) block wins"


async def look_ahead(
    db: AsyncSession, station: Station, days: int = 7, now: datetime | None = None
) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    tz = station_tz(station.timezone)
    # start on a 15-minute boundary
    start = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    end = start + timedelta(days=days)

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

    findings: list[Finding] = []
    open_key = None
    open_finding: Finding | None = None

    def close():
        nonlocal open_key, open_finding
        if open_finding is not None:
            findings.append(open_finding)
        open_key, open_finding = None, None

    at = start
    while at < end:
        if any(b.covers(at) for b in blackouts):
            close()
            at += STEP
            continue
        ranked = svc.rank_matching_blocks(schedules, at, station)
        key = None
        if len(ranked) > 1:
            key = (OVERLAP, tuple(str(b.id) for _, b in ranked))
        elif not ranked:
            key = (GAP,)
        if key != open_key:
            close()
            if key is not None:
                open_key = key
                if key[0] == OVERLAP:
                    winner, losers = ranked[0], ranked[1:]
                    names = ", ".join(f'"{b.name}"' for _, b in losers)
                    tied = all(
                        (s.priority or 0, b.priority or 0)
                        == (winner[0].priority or 0, winner[1].priority or 0)
                        for s, b in losers[:1]
                    )
                    open_finding = Finding(
                        kind=OVERLAP,
                        start=at,
                        end=at + STEP,
                        title=f'"{winner[1].name}" overlaps {names}',
                        detail=f'"{winner[1].name}" plays because {_why(winner, losers[0])}.',
                        resolution=f'"{winner[1].name}" plays; {names} waits.',
                        # Equal priority was settled by a tie-break rule, not by a
                        # choice someone made — worth a look.
                        needs_attention=tied,
                        blocks=[
                            {
                                "id": str(b.id),
                                "name": b.name,
                                "schedule": s.name,
                                "schedule_id": str(s.id),
                                "priority": b.priority or 0,
                                "wins": i == 0,
                            }
                            for i, (s, b) in enumerate(ranked)
                        ],
                    )
                else:
                    open_finding = Finding(
                        kind=GAP,
                        start=at,
                        end=at + STEP,
                        title="Nothing scheduled",
                        detail="No schedule block covers this time.",
                        resolution="The station keeps playing from the automatic queue.",
                        needs_attention=False,
                    )
        elif open_finding is not None:
            open_finding.end = at + STEP
        at += STEP
    close()

    for b in blackouts:
        findings.append(
            Finding(
                kind=OFF_AIR,
                start=max(b.start, start),
                end=min(b.end, end),
                title=f"Off the air: {b.name}",
                detail="Blocks and sponsor spots do not play during this time.",
                resolution="Sponsor spots were moved to open times around it or left out.",
                needs_attention=False,
            )
        )

    _, unplaced = await plan_for_station(db, station, days=days, now=now)
    for u in unplaced:
        if u.blackout is not None:
            continue
        h_start, h_end = {
            "overnight": (0, 6),
            "morning": (6, 12),
            "afternoon": (12, 18),
            "evening": (18, 24),
        }[u.daypart]
        day = datetime(u.local_date.year, u.local_date.month, u.local_date.day, tzinfo=tz)
        findings.append(
            Finding(
                kind=SPOT_UNPLACED,
                start=(day + timedelta(hours=h_start)).astimezone(UTC),
                end=(day + timedelta(hours=h_end)).astimezone(UTC),
                title=f"A sponsor play has no slot ({u.daypart})",
                detail=f'"{u.schedule.name or "Spot schedule"}": {u.reason}.',
                resolution="Not resolved: lower the plays in this daypart or add slot minutes.",
                needs_attention=True,
            )
        )

    findings.sort(key=lambda f: (f.start, f.kind))
    return {
        "station_id": str(station.id),
        "timezone": station.timezone or "UTC",
        "days": days,
        "from": start,
        "to": end,
        "needs_attention": sum(1 for f in findings if f.needs_attention),
        "auto_resolved": sum(1 for f in findings if f.kind == OVERLAP and not f.needs_attention),
        "findings": [
            {
                "kind": f.kind,
                "start": f.start,
                "end": f.end,
                "local_start": f.start.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                "local_end": f.end.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                "title": f.title,
                "detail": f.detail,
                "resolution": f.resolution,
                "needs_attention": f.needs_attention,
                "blocks": f.blocks,
            }
            for f in findings
        ],
    }


async def settle_overlap(db: AsyncSession, winner_id, loser_ids: list) -> ScheduleBlock:
    """Make a manager's choice stick: give the chosen block a priority above the others."""
    ids = [winner_id, *loser_ids]
    rows = (
        (await db.execute(select(ScheduleBlock).where(ScheduleBlock.id.in_(ids)))).scalars().all()
    )
    by_id = {str(b.id): b for b in rows}
    winner = by_id.get(str(winner_id))
    if winner is None:
        raise LookupError("Block not found")
    top = max((b.priority or 0) for b in rows)
    if any(
        str(b.id) != str(winner_id) and (b.priority or 0) >= (winner.priority or 0) for b in rows
    ):
        winner.priority = top + 1
    await db.flush()
    return winner
