"""A due item deleted at the moment the queue starts it must not leave the station idle.

Live check: the refill purge removed a due soft sponsorship slot (the placeholder)
while the song before it was ending. Nothing went on air until a manual skip 29 s later.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.api.v1 import queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


@pytest.mark.asyncio
async def test_slot_deleted_while_starting_falls_through_to_the_next_song(db_session, monkeypatch):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=181)
    spot = Asset(id=uuid.uuid4(), title="Kol B'ramah sponsor Lakewood Bakery", duration=30.0,
                 file_path="https://cdn.example.com/spot.mp3", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    slot = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=3,
                      status="pending", source="ad_slot", timing_mode="soft",
                      preempt_at=datetime.now(UTC) - timedelta(seconds=20))
    db_session.add(slot)
    await db_session.commit()
    slot_id = slot.id

    real_pick = queue_module._pick_next_pending
    deleted = []

    async def pick_then_purged(db, station_id, now_utc):
        if deleted:
            # The other request committed: the slot is gone for every later read
            # (the test shares one session, so the rollback brought it back)
            await db.execute(delete(QueueEntry).where(QueueEntry.id == slot_id)
                             .execution_options(synchronize_session=False))
        entry = await real_pick(db, station_id, now_utc)
        if entry is not None and entry.id == slot_id and not deleted:
            # Another request's refill removes the slot before this advance commits
            await db.execute(delete(QueueEntry).where(QueueEntry.id == slot_id)
                             .execution_options(synchronize_session=False))
            deleted.append(True)
        return entry

    monkeypatch.setattr(queue_module, "_pick_next_pending", pick_then_purged)
    started = await queue_module._check_advance(db_session, station.id)

    assert deleted
    assert started is not None and started.id == pending.id
    rows = (await db_session.execute(
        select(QueueEntry.id, QueueEntry.status).where(QueueEntry.station_id == station.id)
    )).all()
    assert dict(rows)[pending.id] == "playing"
    assert dict(rows)[playing.id] == "played"


@pytest.mark.asyncio
async def test_one_stations_error_does_not_stop_the_others(db_session, monkeypatch):
    """The scheduler shares one database session across all stations in a tick.
    A failed write for one station used to leave it needing a rollback, so every
    station after it failed too, for the rest of the tick."""
    from app.services.scheduler_engine import SchedulerEngine

    broken, _, _ = await _station_with_two_tracks(db_session, elapsed_seconds=10)
    broken.name = "Broken FM"
    await db_session.commit()
    healthy, healthy_playing, healthy_next = await _station_with_two_tracks(db_session, elapsed_seconds=181)
    real = queue_module._check_advance

    async def failing_for_one(db, station_id, early_seconds=0.0):
        if station_id == broken.id:
            # A write that fails at flush, like the stale row in the live case
            dup = QueueEntry(id=healthy_playing.id, station_id=broken.id,
                             asset_id=healthy_playing.asset_id, position=9, status="pending")
            db.add(dup)
            await db.flush()
        return await real(db, station_id, early_seconds)

    monkeypatch.setattr(queue_module, "_check_advance", failing_for_one)
    engine = SchedulerEngine()
    monkeypatch.setattr(engine, "_check_station", _noop_check)
    await engine._check_all_stations(db_session)
    engine._cancel_timed_timers()
    for t in list(engine._advance_timers.values()):
        t.cancel()

    status = (await db_session.execute(
        select(QueueEntry.status).where(QueueEntry.id == healthy_next.id)
    )).scalar_one()
    assert status == "playing"


async def _noop_check(db, station):
    return None
