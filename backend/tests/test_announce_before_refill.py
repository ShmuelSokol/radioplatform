"""A track change is announced before the queue is topped up.

Live (KBR Jerusalem, 09:00): the hourly time check started on the server at
09:00:51 but was announced at 09:02:51, because the advance refilled the queue
before returning and that refill was slow. Listeners meanwhile played the next
song from their own pre-load, then were pulled back to the time check and the
song again. The refill now runs afterwards, apart from the change lock, and the
item on air is announced again when it is done (its "next" may have changed).
"""
import asyncio
import time

import pytest

import app.db.engine as engine_module
from app.api.v1 import queue as queue_module
from tests.conftest import TestSessionLocal
from tests.test_queue_advance import _station_with_two_tracks
from tests.timing import limit


@pytest.mark.asyncio
async def test_change_returns_before_a_slow_refill(db_session, monkeypatch):
    refilled = asyncio.Event()
    announced = []

    async def slow_refill(db, station_id):
        await asyncio.sleep(1.5)
        refilled.set()

    async def record(db, station_id, entry_id, change_fade_seconds=None):
        announced.append(str(entry_id))

    monkeypatch.setattr(queue_module, "_replenish_queue", slow_refill)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", record)
    monkeypatch.setattr(engine_module, "async_session_factory", TestSessionLocal)

    station, _playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=177.5)
    t0 = time.monotonic()
    started = await queue_module._check_advance(db_session, station.id, 3.0)
    assert time.monotonic() - t0 < limit(1.0)
    assert started.id == pending.id
    assert not refilled.is_set()

    # The change lock is free while the refill runs: another change is not held up
    lock = queue_module.advance_lock(station.id)
    assert not lock.locked()

    await asyncio.wait_for(refilled.wait(), 5)
    for _ in range(50):
        if announced:
            break
        await asyncio.sleep(0.05)
    assert announced == [str(pending.id)]
