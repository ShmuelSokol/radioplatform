"""Keep a station's queue in step with its schedule.

A station whose schedule blocks have playlists fills its queue from those blocks a
few hours ahead (playlist_generator_service). When a schedule, block or playlist
changes, the automatic songs not yet played are thrown away and generated again,
so the change is on the air from its start time, not after days of old queue.
"""
import asyncio
import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import ONE_MOMENT_CATEGORIES, Asset
from app.models.playlist_template import PlaylistTemplate
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock

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
    """Pending songs the station chose by itself (not timed, not queued by a person)."""
    return (
        QueueEntry.station_id == station_id,
        QueueEntry.status == "pending",
        QueueEntry.source == "auto",
        QueueEntry.preempt_at.is_(None),
    )


async def drop_dated_clips(db: AsyncSession, station_id) -> int:
    """Weather reports and time checks that were queued as ordinary songs."""
    ids = select(Asset.id).where(Asset.category.in_(ONE_MOMENT_CATEGORIES))
    result = await db.execute(
        delete(QueueEntry)
        .where(*automatic_songs(station_id), QueueEntry.asset_id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


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

    result = await db.execute(
        delete(QueueEntry).where(*automatic_songs(station_id)).execution_options(synchronize_session=False)
    )
    removed = result.rowcount or 0
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
