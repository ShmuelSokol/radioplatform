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
from app.services.spot_schedule_service import DAYPARTS as DAYPART_HOURS
from app.services.spot_schedule_service import load_blackouts, plan_for_station, station_tz
from app.services.station_modes import spots_on

STEP = timedelta(minutes=15)

# kinds
OVERLAP = "overlap"  # two or more blocks cover the same time; one wins
GAP = "gap"  # no block covers this time
SPOT_UNPLACED = "spot_unplaced"  # a sponsor play has no free slot
OFF_AIR = "off_air"  # Shabbos / Yom Tov
BLOCK_ITEM = "block_item"  # a program block has an item with nothing to play
PLAYS = "plays"  # which block is on the air (one line per run of a block)


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
    from app.models.playlist_template import PlaylistTemplate

    template_ids = {b.playlist_template_id for sc in schedules for b in sc.blocks if b.playlist_template_id}
    playlist_names = {}
    if template_ids:
        rows = await db.execute(
            select(PlaylistTemplate.id, PlaylistTemplate.name).where(PlaylistTemplate.id.in_(template_ids))
        )
        playlist_names = {str(r[0]): r[1] for r in rows.all()}
    svc = SchedulingService(db)

    findings: list[Finding] = []
    open_key = None
    open_finding: Finding | None = None

    def close():
        nonlocal open_key, open_finding
        if open_finding is not None:
            findings.append(open_finding)
        open_key, open_finding = None, None

    def step_end(t: datetime) -> datetime:
        """End of the step starting at t, cut short where an off-air window begins."""
        stop = t + STEP
        for b in blackouts:
            if t < b.start < stop:
                stop = b.start
        return stop

    # What is on the air: a run of the same winning block is one line.
    playing: Finding | None = None
    playing_id = None

    def close_playing():
        nonlocal playing, playing_id
        if playing is not None:
            findings.append(playing)
        playing, playing_id = None, None

    at = start
    while at < end:
        if any(b.covers(at) for b in blackouts):
            close()
            close_playing()
            at += STEP
            continue
        everything = svc.rank_matching_blocks(schedules, at, station)
        top = everything[0] if everything else None
        if (str(top[1].id) if top else None) != playing_id:
            close_playing()
            if top is not None:
                s, b = top
                playlist = playlist_names.get(str(b.playlist_template_id))
                playing_id = str(b.id)
                playing = Finding(
                    kind=PLAYS,
                    start=at,
                    end=step_end(at),
                    title=f'"{b.name}"',
                    detail=(
                        f'Plays the playlist "{playlist}".'
                        if playlist
                        else "Plays its own songs." if b.playlist_template_id is None
                        else "Plays its playlist."
                    ),
                    resolution=f'Schedule "{s.name}".',
                    needs_attention=False,
                    blocks=[{"id": str(b.id), "name": b.name, "schedule": s.name,
                             "schedule_id": str(s.id), "priority": b.priority or 0, "wins": True}],
                )
        elif playing is not None:
            playing.end = step_end(at)
        # A timed block over the running playlist is how the schedule is meant
        # to work, not a collision; only timed blocks can collide with each other.
        ranked = [sb for sb in everything if not getattr(sb[1], "until_next", False)]
        key = None
        if not ranked and everything:
            pass
        elif len(ranked) > 1:
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
                        end=step_end(at),
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
                        end=step_end(at),
                        title="Nothing scheduled",
                        detail="No schedule block covers this time.",
                        resolution="The station keeps playing from the automatic queue.",
                        needs_attention=False,
                    )
        elif open_finding is not None:
            open_finding.end = step_end(at)
        at += STEP
    close()
    close_playing()

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
    unplaced = [u for u in unplaced if u.blackout is None]
    sponsor_names: dict[str, str] = {}
    if unplaced:
        from app.models.sponsor import Sponsor

        rows = await db.execute(
            select(Sponsor.id, Sponsor.name).where(
                Sponsor.id.in_({u.schedule.sponsor_id for u in unplaced})
            )
        )
        sponsor_names = {str(r[0]): r[1] for r in rows.all()}
    # One line per sponsorship, day, part of the day and reason, with a count.
    grouped: dict[tuple, list] = {}
    for u in unplaced:
        key = (str(u.schedule.id), u.local_date, u.daypart, u.reason)
        grouped.setdefault(key, []).append(u)
    for (_, local_date, daypart, reason), group in grouped.items():
        u = group[0]
        h_start, h_end = DAYPART_HOURS[daypart]
        day = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
        who = sponsor_names.get(str(u.schedule.sponsor_id)) or u.schedule.name or "A sponsorship"
        n = len(group)
        plays = "1 play" if n == 1 else f"{n} plays"
        if "sponsor place" in reason:
            fix = (
                "Not resolved: add sponsor places to a block in this time of day, "
                "add another block, or lower the plays."
            )
        elif "already taken" in reason:
            fix = "Not resolved: pick another exact time for one of them."
        else:
            fix = "Not resolved: lower the plays in this time of day or add spot times."
        findings.append(
            Finding(
                kind=SPOT_UNPLACED,
                start=(day + timedelta(hours=h_start)).astimezone(UTC),
                end=(day + timedelta(hours=h_end)).astimezone(UTC),
                title=f"{who}: {plays} with no place ({daypart})",
                detail=f"{reason[0].upper()}{reason[1:]}.",
                resolution=fix,
                needs_attention=True,
            )
        )

    # Program blocks ("Weather Sponsors"): an item that has nothing to play.
    from app.services.program_block_service import plan_blocks

    seen: set[tuple] = set()
    for occurrence in await plan_blocks(db, station, hours=days * 24, now=now):
        for problem in occurrence.problems:
            key = (str(occurrence.block.id), problem)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    kind=BLOCK_ITEM,
                    start=occurrence.at,
                    end=occurrence.at + timedelta(minutes=1),
                    title=f'"{occurrence.block.name}" has an item with nothing to play',
                    detail=f"{problem}.",
                    resolution="The rest of the block plays; this item is left out. "
                    "Fix it on the Program Blocks page.",
                    needs_attention=True,
                )
            )

    findings.sort(key=lambda f: (f.start, f.kind))
    return {
        "station_id": str(station.id),
        "timezone": station.timezone or "UTC",
        "days": days,
        # Spots mode off (control room): no sponsor spot is planned, so none shows as missing
        "spots_off": not spots_on(station),
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
