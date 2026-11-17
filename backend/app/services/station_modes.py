"""
Two station-wide switches from KBR Studio's control panel (see wiki/station-modes.md):

HS (hard start), on by default. On: timed items (time check, weather, blocks marked
hard) start on the second and the song on air fades. Off: every timed item waits
for the song on air to finish, like a soft block. Shabbos/Yom Tov silence and live
show hard stops are never softened. Switching HS back on restores exactly what it
softened.

Spots mode, on by default. Off: no sponsorship spots are planned into the queue
(ad slots and the sponsor places inside blocks stay empty); on again: the spot
schedules fill them as before.

Both live in Station.automation_config ("hard_start", "spots_on"); missing = on.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.hs_softened import HsSoftened
from app.models.queue_entry import QueueEntry, hard_timed
from app.models.station import Station

logger = logging.getLogger(__name__)

HS_KEY = "hard_start"
SPOTS_KEY = "spots_on"
# Where the first version kept softened timings (now the hs_softened table)
LEGACY_SOFTENED_KEY = "hs_softened"


def hard_start_on(station: Station | None) -> bool:
    return (station.automation_config or {}).get(HS_KEY, True) is not False if station else True


def spots_on(station: Station | None) -> bool:
    return (station.automation_config or {}).get(SPOTS_KEY, True) is not False if station else True


def modes(station: Station | None) -> dict:
    return {"hard_start": hard_start_on(station), "spots": spots_on(station)}


async def soften_hard_items(db: AsyncSession, station: Station) -> int:
    """HS off: make every pending hard timed item wait for the song on air.

    Called on each scheduler pass (new items keep arriving). Remembers each item's
    timing in its own HsSoftened row so HS on can put it back. Never writes the
    station's settings, so it cannot undo a switch flipped meanwhile. Does not commit.
    """
    if hard_start_on(station):
        return 0
    silence_ids = select(Asset.id).where(Asset.asset_type == "silence")
    rows = (await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station.id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            # hard_timed() misses old entries with no timing and no source (SQL NULL
            # != 'ad_slot' is not true), yet they start on the second: soften them too
            or_(hard_timed(), QueueEntry.hard_by.isnot(None),
                and_(QueueEntry.timing_mode.is_(None), QueueEntry.source.is_(None))),
            # Shabbos / Yom Tov silence must start on time
            or_(QueueEntry.asset_id.is_(None), QueueEntry.asset_id.notin_(silence_ids)),
            # Resumed shiurim and live-show entries are not "timed songs"
            or_(QueueEntry.source.is_(None), and_(QueueEntry.source != "live", ~QueueEntry.source.like("resume@%"))),
        )
    )).scalars().all()
    if not rows:
        return 0
    known = set((await db.execute(
        select(HsSoftened.entry_id).where(HsSoftened.entry_id.in_([e.id for e in rows]))
    )).scalars().all())
    known = {str(k) for k in known}
    for e in rows:
        if str(e.id) not in known:
            db.add(HsSoftened(entry_id=e.id, station_id=station.id,
                              timing_mode=e.timing_mode, hard_by=e.hard_by))
        e.timing_mode = "soft"
        e.hard_by = None
    await db.flush()
    logger.info("Station %s: HS off, %d timed item(s) now wait for the song", station.id, len(rows))
    return len(rows)


def _restore(e: QueueEntry, mode: str | None, hard_by: datetime | None) -> None:
    # The old rule (mode None) treats anything but an ad slot as hard: say so plainly
    e.timing_mode = mode if mode in ("hard", "soft") else ("soft" if e.source == "ad_slot" else "hard")
    if hard_by is not None and hard_by.tzinfo is None:
        hard_by = hard_by.replace(tzinfo=UTC)
    e.hard_by = hard_by


async def restore_hard_items(db: AsyncSession, station: Station) -> int:
    """HS on again: put back the timing of every item HS off softened. Does not commit."""
    saved = {str(r.entry_id): (r.timing_mode, r.hard_by) for r in (await db.execute(
        select(HsSoftened).where(HsSoftened.station_id == station.id)
    )).scalars().all()}
    # Saved by the first version, inside the station's settings
    if LEGACY_SOFTENED_KEY in (station.automation_config or {}):
        config = dict(station.automation_config)
        for k, (mode, hard_by) in (config.pop(LEGACY_SOFTENED_KEY) or {}).items():
            saved.setdefault(k, (mode, datetime.fromisoformat(hard_by) if hard_by else None))
        station.automation_config = config
    if not saved:
        return 0
    rows = (await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station.id,
            QueueEntry.status == "pending",
            QueueEntry.id.in_([uuid.UUID(k) for k in saved]),
        )
    )).scalars().all()
    for e in rows:
        _restore(e, *saved[str(e.id)])
    await db.execute(delete(HsSoftened).where(HsSoftened.station_id == station.id))
    await db.flush()
    return len(rows)


async def set_modes(db: AsyncSession, station: Station, hard_start: bool | None, spots: bool | None) -> dict:
    """Change one or both switches and bring the queue in line. Does not commit.
    Call under the station's advance_lock: the settings are read fresh here so a
    change saved elsewhere a moment ago is kept."""
    await db.refresh(station, ["automation_config"])
    config = dict(station.automation_config or {})
    if hard_start is not None:
        config[HS_KEY] = bool(hard_start)
    if spots is not None:
        config[SPOTS_KEY] = bool(spots)
    station.automation_config = config
    await db.flush()

    if hard_start is True:
        await restore_hard_items(db, station)
    elif hard_start is False:
        await soften_hard_items(db, station)

    if spots is not None:
        from app.services.program_block_service import reconcile_block_entries
        from app.services.spot_schedule_service import reconcile_spot_slots

        await reconcile_spot_slots(db, station.id)
        await reconcile_block_entries(db, station.id, rebuild=True)
        if not hard_start_on(station):
            # Rebuilt block items come back hard: HS is still off
            await soften_hard_items(db, station)
    await db.flush()
    return modes(station)
