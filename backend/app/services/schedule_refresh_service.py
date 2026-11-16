"""Keep a station's queue in step with its schedule.

A station whose schedule blocks have playlists fills its queue from those blocks a
few hours ahead (playlist_generator_service). When a schedule, block or playlist
changes, the automatic songs not yet played are thrown away and generated again,
so the change is on the air from its start time, not after days of old queue.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import ONE_MOMENT_CATEGORIES, Asset
from app.models.playlist_template import PlaylistTemplate
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.station import Station

logger = logging.getLogger(__name__)

# One refresh at a time per station (several quick edits, e.g. reordering steps)
_locks: dict[str, asyncio.Lock] = {}


async def uses_block_playlists(db: AsyncSession, station_id) -> bool:
    """True when an active schedule of the station has a block with an active playlist."""
    row = await db.execute(
        select(ScheduleBlock.id)
        .join(Schedule, Schedule.id == ScheduleBlock.schedule_id)
        .join(PlaylistTemplate, PlaylistTemplate.id == ScheduleBlock.playlist_template_id)
        .where(
            Schedule.station_id == station_id,
            Schedule.is_active.is_(True),
            PlaylistTemplate.is_active.is_(True),
        )
        .limit(1)
    )
    return row.first() is not None


def automatic_songs(station_id):
    """Pending songs the station chose by itself (not timed, not queued by a person).

    A song the playout engine already holds as its next track is on its way to air
    and is left alone: deleting it lost track of what Main was playing."""
    from app.services.playout_engine import held_entry_ids

    conditions = [
        QueueEntry.station_id == station_id,
        QueueEntry.status == "pending",
        QueueEntry.source == "auto",
        QueueEntry.preempt_at.is_(None),
    ]
    held = held_entry_ids(station_id)
    if held:
        conditions.append(QueueEntry.id.notin_(held))
    return tuple(conditions)


async def drop_dated_clips(db: AsyncSession, station_id) -> int:
    """Weather reports and time checks that were queued as ordinary songs."""
    ids = select(Asset.id).where(Asset.category.in_(ONE_MOMENT_CATEGORIES))
    result = await db.execute(
        delete(QueueEntry)
        .where(*automatic_songs(station_id), QueueEntry.asset_id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def remove_automatic_songs(db: AsyncSession, station_id) -> int:
    """Throw away the unplayed automatic songs so they are drawn again.

    Each block's place in its playlist goes back by the songs removed that were
    drawn for it, so the redrawn queue carries on from the same step.
    """
    from app.services.playlist_generator_service import CONFIG_KEY

    counts = await db.execute(
        select(QueueEntry.block_id, func.count())
        .where(*automatic_songs(station_id), QueueEntry.block_id.isnot(None))
        .group_by(QueueEntry.block_id)
    )
    back = {block_id: n for block_id, n in counts.all()}
    result = await db.execute(
        delete(QueueEntry).where(*automatic_songs(station_id)).execution_options(synchronize_session=False)
    )
    if back:
        station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one()
        config = dict(station.automation_config or {})
        saved = dict(config.get(CONFIG_KEY) or {})
        cursor = dict(saved.get("cursor") or {})
        for block_id, n in back.items():
            if block_id in cursor:
                cursor[block_id] = int(cursor[block_id]) - n
        saved["cursor"] = cursor
        config[CONFIG_KEY] = saved
        station.automation_config = config
    await db.flush()
    return result.rowcount or 0


# How long before a block with a playlist starts (or the playlist changes) the
# automatic songs are drawn again, from the real time on air. Songs drawn hours
# earlier were placed by expected lengths, and small differences add up.
REALIGN_WINDOW = timedelta(minutes=15)
# station id -> the change the queue was last redrawn for
_realigned: dict[str, str] = {}


async def realign_before_block_change(db: AsyncSession, station: Station, now: datetime) -> bool:
    """Redraw the automatic songs once, shortly before the playlist on the air changes.

    Returns True when the songs were thrown away (the caller fills again).
    """
    from app.services.scheduling import SchedulingService

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
    if not schedules:
        return False
    svc = SchedulingService(db)

    def playlist_block(at: datetime) -> str | None:
        ranked = svc.rank_matching_blocks(schedules, at, station)
        block = ranked[0][1] if ranked else None
        if block is None or not block.playlist_template_id:
            return None
        template = block.playlist_template
        if template is None or not template.is_active or not template.slots:
            return None
        return str(block.id)

    on_air = playlist_block(now)
    # Whole minutes, so the same change is recognised on every pass
    at = now.replace(second=0, microsecond=0)
    change = None
    while at < now + REALIGN_WINDOW:
        at += timedelta(minutes=1)
        block_id = playlist_block(at)
        if block_id != on_air:
            change = f"{block_id}@{at:%Y-%m-%dT%H:%M}"
            break
    if change is None or _realigned.get(str(station.id)) == change:
        return False
    _realigned[str(station.id)] = change
    removed = await remove_automatic_songs(db, station.id)
    logger.info("Station %s: redrew %d automatic songs before the playlist changes (%s)", station.id, removed, change)
    return True


async def refresh_station(db: AsyncSession, station_id, *, had_playlists: bool = False) -> int:
    """Regenerate the automatic part of the queue after a schedule change.

    Only for stations that fill from their blocks (now, or before the change when
    `had_playlists`); others keep their ordinary fill. Returns songs removed.
    """
    if not (had_playlists or await uses_block_playlists(db, station_id)):
        return 0
    async with _locks.setdefault(str(station_id), asyncio.Lock()):
        return await _refresh(db, station_id)


async def _refresh(db: AsyncSession, station_id) -> int:
    from app.services.queue_replenish_service import QueueReplenishService

    removed = await remove_automatic_songs(db, station_id)
    await db.commit()
    # The change was just made in this session; read blocks and playlists afresh
    db.expire_all()
    try:
        await QueueReplenishService(db, station_id).replenish()
        await db.commit()
    except Exception:
        logger.exception("Queue refill after a schedule change failed for station %s", station_id)
        await db.rollback()
    logger.info("Schedule change: replaced %d automatic songs on station %s", removed, station_id)
    return removed


async def station_of_schedule(db: AsyncSession, schedule_id) -> uuid.UUID | None:
    row = await db.execute(select(Schedule.station_id).where(Schedule.id == schedule_id))
    return row.scalar_one_or_none()


async def stations_using_template(db: AsyncSession, template_id) -> list:
    rows = await db.execute(
        select(Schedule.station_id)
        .join(ScheduleBlock, ScheduleBlock.schedule_id == Schedule.id)
        .where(ScheduleBlock.playlist_template_id == template_id, Schedule.is_active.is_(True))
        .distinct()
    )
    return [r[0] for r in rows.all()]
