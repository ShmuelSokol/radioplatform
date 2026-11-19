"""The last word on a station ID, at the moment it would go on air.

The fill places IDs between songs by predicting when everything will air
(`StationIdSprinkler`, playlist_generator_service.py). A soft break (:15, :30, :45)
waits for the song on air to end, so at air time it can land just before an ID the
fill had put minutes away from it: the simulator (2026-11-19) heard a break and then
an ID 0.9 minutes later, spoken next to spoken. So both pick paths (the browser
player's `_pick_next_pending` and the engine's `pick_next`) ask here first, and an
automatically queued ID is passed over, for good, when

- the item before it (the one on air now) is not a programme item (a break, a time
  check, a sponsorship, another jingle...), or
- a timed item is due within the station's `clear_minutes` of it, or
- it is shorter than MIN_STATION_ID_SECONDS (4 s), or
- the last ID aired less than the station's `min_minutes` ago.

Passing one over only makes the gap to the next ID longer. The browser path marks it
skipped at once; the engine's pick is read-only, so the IDs it passed over are kept
here and marked skipped at the next start report (`take_passed`).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.asset import MIN_STATION_ID_SECONDS, STATION_ID_CATEGORY_PREFIX
from app.models.queue_entry import QueueEntry

# Queue sources a person chose: never second-guessed
_PERSON_SOURCES = ("manual", "request", "loop")

# Engine stations: entry ids passed over, per station, until the next start report
_passed: dict[str, set[str]] = {}


def is_auto_station_id(entry: QueueEntry) -> bool:
    asset = getattr(entry, "asset", None)
    category = (getattr(asset, "category", None) or "").lower()
    return (
        category.startswith(STATION_ID_CATEGORY_PREFIX)
        and entry.preempt_at is None
        and (entry.source or "auto") not in _PERSON_SOURCES
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def must_wait(db: AsyncSession, station_id, entry: QueueEntry, now: datetime) -> bool:
    """True when this automatically queued station ID must not air now (see above)."""
    if not is_auto_station_id(entry):
        return False
    from app.models.station import Station
    from app.services.playlist_generator_service import NOT_BEFORE_AN_ID, id_settings_for, on_air_seconds

    if float(getattr(entry.asset, "duration", None) or 0.0) < MIN_STATION_ID_SECONDS:
        return True  # too short for the engine to load the next song behind it
    station = await db.get(Station, station_id)
    settings = id_settings_for(station.automation_config if station is not None else None)
    playing = (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset))
        # what is on air (or, when a skip already closed it, what aired last)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["playing", "played"]),
               QueueEntry.started_at.isnot(None), QueueEntry.started_at > now - timedelta(hours=3),
               QueueEntry.id != entry.id)
        .order_by(QueueEntry.started_at.desc())
        .limit(1)
    )).unique().scalar_one_or_none()
    start = now
    if playing is not None:
        before = playing.asset
        kind = (getattr(before, "asset_type", None) or "").lower()
        if playing.preempt_at is not None or kind in NOT_BEFORE_AN_ID or is_auto_station_id(playing):
            return True
        if playing.started_at is not None and before is not None:
            ends = _aware(playing.started_at) + timedelta(
                seconds=on_air_seconds(before.duration, before.metadata_extra)
            )
            start = max(start, ends)
    # Not sooner than the station's shortest gap after the last ID that aired: the fill
    # spaced them by predicted times, and a cut song or a moved break brings them closer
    # (simulator: 10.2 minutes apart). 
    from app.models.asset import Asset

    last_id = (await db.execute(
        select(QueueEntry.started_at).join(Asset, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["playing", "played"]),
               QueueEntry.started_at.isnot(None), QueueEntry.id != entry.id,
               QueueEntry.started_at > start - timedelta(minutes=settings.min_minutes),
               func.lower(Asset.category).like(f"{STATION_ID_CATEGORY_PREFIX}%"))
        .order_by(QueueEntry.started_at.desc()).limit(1)
    )).scalar()
    if last_id is not None and start - _aware(last_id) < timedelta(minutes=settings.min_minutes):
        return True
    length = float(getattr(entry.asset, "duration", None) or 10.0)
    margin = timedelta(minutes=settings.clear_minutes)
    # Half a minute more than the setting ahead: the start is an estimate (the engine asks
    # for the next item before the one now starting is reported), and the simulator
    # found an ID 2 min 59.9 s before a :30 time check
    ahead = margin + timedelta(seconds=30)
    due = (await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at < start + timedelta(seconds=length) + ahead,
        ).limit(1)
    )).first()
    if due is not None:
        return True
    # ... and behind: a break that went on air less than clear_minutes before (a short
    # song between them is not enough; simulator: an ID 2.8 min after a soft break)
    recent = (await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status.in_(["playing", "played"]),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.started_at.isnot(None),
            QueueEntry.started_at > start - margin,
        ).limit(1)
    )).first()
    return recent is not None


def pass_over(station_id, entry_id) -> None:
    _passed.setdefault(str(station_id), set()).add(str(entry_id))


def passed(station_id) -> set[str]:
    return _passed.get(str(station_id), set())


async def take_passed(db: AsyncSession, station_id) -> int:
    """Mark the IDs the engine's pick passed over as skipped (start report)."""
    ids = _passed.pop(str(station_id), set())
    if not ids:
        return 0
    from sqlalchemy import update

    uuids = []
    for x in ids:
        try:
            uuids.append(uuid.UUID(x))
        except ValueError:
            pass
    if not uuids:
        return 0
    res = await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.id.in_(uuids))
        .values(status="skipped")
        .execution_options(synchronize_session=False)
    )
    return res.rowcount or 0
