"""A timed item due seconds after a song change starts at the change.

Live check (KBR Jerusalem, 08:35:00): the song before a block ended a few seconds
early and the block's first item waited for the clock, leaving 3.3 s of quiet.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1 import queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


async def _song_ending_with_timed_item(db, due_in, mode):
    # 180 s song at its crossfade point (177.5 s in), a song next, a timed item after
    station, playing, pending = await _station_with_two_tracks(db, elapsed_seconds=177.5)
    item = Asset(id=uuid.uuid4(), title="Block opener", duration=20.0,
                 file_path="https://cdn.example.com/opener.mp3", asset_type="jingle")
    db.add(item)
    await db.flush()
    timed = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=item.id, position=3,
                       status="pending", source="block", timing_mode=mode,
                       preempt_at=datetime.now(UTC) + timedelta(seconds=due_in))
    db.add(timed)
    await db.commit()
    return station, pending, item


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hard", "soft"])
async def test_item_due_in_seconds_starts_at_the_change(db_session, mode):
    station, _, item = await _song_ending_with_timed_item(db_session, due_in=5, mode=mode)
    started = await queue_module._check_advance(db_session, station.id, 3.0)
    assert str(started.asset_id) == str(item.id)


@pytest.mark.asyncio
async def test_item_due_later_waits(db_session):
    station, pending, _ = await _song_ending_with_timed_item(db_session, due_in=60, mode="hard")
    started = await queue_module._check_advance(db_session, station.id, 3.0)
    assert str(started.asset_id) == str(pending.asset_id)
