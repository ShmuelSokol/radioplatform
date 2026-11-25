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
from app.models.playlist_entry import PlaylistEntry
from app.models.playlist_template import PlaylistTemplate
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.station import Station

logger = logging.getLogger(__name__)

# One refresh at a time per station (several quick edits, e.g. reordering steps)
_locks: dict[str, asyncio.Lock] = {}


async def uses_block_playlists(db: AsyncSession, station_id) -> bool:
    """True when an active schedule of the station has a block with an active
    playlist, or a block with its own songs ("+ Add song")."""
    songs = await db.execute(
        select(ScheduleBlock.id)
        .join(Schedule, Schedule.id == ScheduleBlock.schedule_id)
        .join(PlaylistEntry, PlaylistEntry.block_id == ScheduleBlock.id)
        .where(
            Schedule.station_id == station_id,
            Schedule.is_active.is_(True),
            PlaylistEntry.is_enabled.is_(True),
        )
        .limit(1)
    )
    if songs.first() is not None:
        return True
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


async def songs_to_replace(db: AsyncSession, station_id, *, keep_next: int = 0) -> tuple[list, dict]:
    """The unplayed automatic songs a redraw replaces, and how far each block's
    place in its playlist goes back for them. Reads only: nothing is locked while
    the new songs are picked, and the swap itself is done by the fill in one short
    transaction (QueueReplenishService._fill_from_playlists)."""
    which = automatic_songs(station_id)
    rows = (
        await db.execute(
            select(QueueEntry.id, QueueEntry.block_id).where(*which).order_by(QueueEntry.position)
        )
    ).all()
    rows = rows[keep_next:]
    back: dict = {}
    for _id, block_id in rows:
        if block_id is not None:
            back[block_id] = back.get(block_id, 0) + 1
    return [r[0] for r in rows], back


async def remove_automatic_songs(db: AsyncSession, station_id, *, keep_next: int = 0) -> int:
    """Throw away the unplayed automatic songs so they are drawn again.

    Each block's place in its playlist goes back by the songs removed that were
    drawn for it, so the redrawn queue carries on from the same step.

    keep_next leaves the first few in place. Callers that redraw inside one long
    transaction use it: the player may move on to the next song meanwhile, and a
    row deleted but not yet committed would hold it until the redraw is done.
    """
    from app.services.playlist_generator_service import CONFIG_KEY

    which = automatic_songs(station_id)
    if keep_next:
        head = (
            await db.execute(
                select(QueueEntry.id).where(*which).order_by(QueueEntry.position).limit(keep_next)
            )
        ).scalars().all()
        if head:
            which = (*which, QueueEntry.id.notin_(head))
    counts = await db.execute(
        select(QueueEntry.block_id, func.count())
        .where(*which, QueueEntry.block_id.isnot(None))
        .group_by(QueueEntry.block_id)
    )
    back = {block_id: n for block_id, n in counts.all()}
    result = await db.execute(
        delete(QueueEntry).where(*which).execution_options(synchronize_session=False)
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


async def realign_before_block_change(
    db: AsyncSession, station: Station, now: datetime, *, remove: bool = True
) -> bool:
    """Redraw the automatic songs once, shortly before the playlist on the air changes.

    Returns True when the songs were thrown away (the caller fills again). With
    `remove=False` nothing is deleted here: the caller replaces them itself.
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
    from app.services.playlist_generator_service import block_songs

    svc = SchedulingService(db)
    with_songs = set(await block_songs(db, schedules))

    def playlist_block(at: datetime) -> str | None:
        ranked = svc.rank_matching_blocks(schedules, at, station)
        block = ranked[0][1] if ranked else None
        if block is not None and str(block.id) in with_songs:
            return str(block.id)
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
    # A hard timed item at the change (the time check on the hour) starts the new block
    # on time by itself, and the songs before it have been fitted to end on it
    # (hour_fit): only the songs after it are drawn again. Drawing the run-up again
    # threw that fit away: Main 2026-11-25, redrawn at 5:45, lost 70 s of a medley at
    # 6:00 (423 s of a track in the simulator).
    keep = await _songs_before_hard_change(db, station, now, at)
    _realign_keep[str(station.id)] = keep
    if remove:
        removed = await remove_automatic_songs(db, station.id, keep_next=max(KEEP_NEXT, keep or 0))
        logger.info(
            "Station %s: redrew %d automatic songs before the playlist changes (%s)", station.id, removed, change
        )
    else:
        logger.info("Station %s: redrawing the automatic songs before the playlist changes (%s)%s", station.id, change,
                    f", keeping the {keep} before the hard item there" if keep is not None else "")
    return True


# station id -> how many automatic songs the last realign keeps (None: KEEP_NEXT)
_realign_keep: dict[str, int | None] = {}


def realign_keep(station_id) -> int | None:
    """The songs the last realign_before_block_change keeps: the run-up to a hard
    timed item at the change, or None (KEEP_NEXT)."""
    return _realign_keep.pop(str(station_id), None)


async def _songs_before_hard_change(db: AsyncSession, station: Station, now: datetime, change: datetime) -> int | None:
    """When a hard timed item falls on the block change: how many of the automatic
    songs (in queue order) start before it, by the same lengths the fit uses. None
    when there is no hard item there."""
    from app.config import settings
    from app.models.queue_entry import hard_timed, is_soft_timed
    from app.services.hour_fit import EARLY_START, _aware, _seconds, on_air_until

    hard = (
        await db.execute(
            select(QueueEntry.preempt_at).where(
                QueueEntry.station_id == station.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at >= change - timedelta(minutes=1),
                QueueEntry.preempt_at <= change + timedelta(minutes=1),
                hard_timed(),
            ).order_by(QueueEntry.preempt_at).limit(1)
        )
    ).scalar_one_or_none()
    if hard is None:
        return None
    hard = _aware(hard)
    engine = settings.engine_drives(station.id)
    rows = (
        await db.execute(
            select(QueueEntry)
            .options(selectinload(QueueEntry.asset))
            .where(QueueEntry.station_id == station.id, QueueEntry.status.in_(["pending", "playing"]))
            .order_by(QueueEntry.position)
        )
    ).scalars().all()
    automatic = set(
        (await db.execute(select(QueueEntry.id).where(*automatic_songs(station.id)))).scalars().all()
    )
    at = now
    playing = next((e for e in rows if e.status == "playing"), None)
    if playing is not None and playing.started_at is not None:
        until = on_air_until(playing, engine)
        if until is not None:
            at = max(now, until)
    timed = sorted((e for e in rows if e.status == "pending" and e.preempt_at is not None and _aware(e.preempt_at) < hard),
                   key=lambda e: _aware(e.preempt_at))
    keep = 0
    for e in rows:
        if e.status != "pending" or e.preempt_at is not None:
            continue
        while timed and _aware(timed[0].preempt_at) <= at + timedelta(seconds=EARLY_START):
            item = timed.pop(0)
            due = _aware(item.preempt_at)
            if not is_soft_timed(item) and due > at:
                at = due
            at += timedelta(seconds=_seconds(item, engine) or 30.0)
        if at >= hard - timedelta(seconds=EARLY_START):
            break
        if e.id in automatic:
            keep += 1
        at += timedelta(seconds=_seconds(e, engine) or 180.0)
    return keep


# station id -> the blackout end the songs after it were last redrawn for
_blackout_redrawn: dict[str, str] = {}


async def redraw_before_blackout_end(db: AsyncSession, station: Station, now: datetime) -> bool:
    """Shortly before Shabbos or Yom Tov ends, draw the songs after it again.

    The queue is not topped up during a blackout, so the redraw a block gets 15
    minutes before it starts (`realign_before_block_change`) never runs for the
    block that begins as the station comes back (Motzei Shabbos, sunset + 72 min).
    The songs waiting then were drawn a day or more earlier, and songs drawn for
    before the blackout that did not air were still ahead of them. Once per
    blackout, REALIGN_WINDOW before its end: every unplayed automatic song is
    replaced from the real end time, so the first song back is the first step of
    the block that starts then. New songs go after the silences (positions after
    them), which the blackout's own clean-up leaves alone. Returns True when it ran.
    """
    from app.models.holiday_window import HolidayWindow
    from app.services.station_service import window_covers_station

    windows = (
        await db.execute(
            select(HolidayWindow).where(
                HolidayWindow.is_blackout.is_(True),
                HolidayWindow.start_datetime <= now,
                HolidayWindow.end_datetime > now,
                HolidayWindow.end_datetime <= now + REALIGN_WINDOW,
            )
        )
    ).scalars().all()
    window = next((w for w in windows if window_covers_station(w, station.id)), None)
    if window is None:
        return False
    key = f"{window.id}@{window.end_datetime:%Y-%m-%dT%H:%M}"
    if _blackout_redrawn.get(str(station.id)) == key:
        return False
    _blackout_redrawn[str(station.id)] = key
    if not await uses_block_playlists(db, station.id):
        return False
    from app.services.queue_replenish_service import QueueReplenishService

    service = QueueReplenishService(db, station.id)
    service.replace_all = True
    service.keep_next = 0  # nothing is on air but silence: every waiting song is redrawn
    await service.replenish()
    logger.info(
        "Station %s: songs after the blackout ending %s drawn again (%d replaced)",
        station.id, window.end_datetime.isoformat(), service.replaced,
    )
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
    removed = await replace_automatic_songs(db, station_id)
    logger.info("Schedule change: replaced %d automatic songs on station %s", removed, station_id)
    return removed


# The next automatic song stays while the rest are redrawn (see remove_automatic_songs)
KEEP_NEXT = 1


async def replace_automatic_songs(db: AsyncSession, station_id) -> int:
    """Draw the unplayed automatic songs again. Returns songs removed (0 when the
    refill failed; nothing changes then).

    A station that fills from playlists: the new songs are picked first, with the
    old ones only left out of the count, and then old and new are swapped in one
    short transaction. Picking takes seconds on the live database (longer on the
    first draw after a restart, which reads the play history); done after the
    delete, as it was until 2026-11-18, it held the deleted rows locked all that
    time, and song changes, start reports from the playout engine and library
    deletes waited behind it.

    A station that fills from schedule rules: the old songs are deleted and the
    fill's own commits are held back until the new ones are written, so the
    player never sees a queue without songs (Jerusalem had none from 16:21:34 to
    16:22:31 on 2026-11-17 when the delete was committed first).
    """
    from app.services.playlist_generator_service import settings_for
    from app.services.queue_replenish_service import QueueReplenishService

    await db.commit()  # the caller's own change is saved whatever happens below
    # The change was just made in this session; read blocks and playlists afresh
    db.expire_all()
    config = (
        await db.execute(select(Station.automation_config).where(Station.id == station_id))
    ).scalar_one_or_none()
    if settings_for(config).enabled or await uses_block_playlists(db, station_id):
        from app.api.v1.queue import _refill_lock

        service = QueueReplenishService(db, station_id)
        service.replace_all = True
        try:
            # One refill of a station at a time: a top-up after a song change
            # that ran beside the redraw added songs the redraw had not counted.
            async with _refill_lock(station_id):
                await service.replenish()
                await db.commit()
        except Exception:
            logger.exception("Queue refill after a schedule change failed for station %s", station_id)
            await db.rollback()
            return 0
        return service.replaced

    db.commit = db.flush  # replenish commits in batches; hold them until the swap
    try:
        removed = await remove_automatic_songs(db, station_id, keep_next=KEEP_NEXT)
        db.expire_all()
        await QueueReplenishService(db, station_id).replenish()
    except Exception:
        logger.exception("Queue refill after a schedule change failed for station %s", station_id)
        del db.commit
        await db.rollback()
        return 0
    del db.commit
    await db.commit()
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
