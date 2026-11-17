"""A hard-timed item starts at its time, not at the next 3 s poll.

Measured by listening: hard sponsorships and the hourly time started 1 to 3 s
late while the control room had already faded the song out at the due time.
"""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.services.scheduler_engine import SchedulerEngine
from tests.test_queue_advance import _station_with_two_tracks


async def _fire_times(db, monkeypatch, **entry_fields):
    station, playing, pending = await _station_with_two_tracks(db, elapsed_seconds=10.0)
    spot = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0,
                 file_path="https://cdn.example.com/spot.mp3", asset_type="spot")
    db.add(spot)
    await db.flush()
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=3,
                      status="pending", **entry_fields))
    await db.commit()
    engine = SchedulerEngine()
    fired = []

    async def record(station_id, crossfade_seconds=0.0):
        fired.append(datetime.now(UTC))

    monkeypatch.setattr(engine, "_precise_advance", record)
    await engine._arm_timed_start(db, station.id)
    await engine._arm_timed_start(db, station.id)  # the next poll must not arm a second timer
    await asyncio.sleep(1.6)
    engine._cancel_timed_timers()
    return fired


@pytest.mark.asyncio
async def test_hard_item_fires_at_its_time(db_session, monkeypatch):
    due = datetime.now(UTC) + timedelta(seconds=1.2)
    fired = await _fire_times(db_session, monkeypatch, source="ad_slot", timing_mode="hard", preempt_at=due)
    assert len(fired) == 1
    assert 0 <= (fired[0] - due).total_seconds() < 0.1


@pytest.mark.asyncio
async def test_soft_item_fires_when_its_wait_runs_out(db_session, monkeypatch):
    now = datetime.now(UTC)
    hard_by = now + timedelta(seconds=1.2)
    fired = await _fire_times(db_session, monkeypatch, source="ad_slot", timing_mode="soft",
                              preempt_at=now - timedelta(seconds=30), hard_by=hard_by)
    assert len(fired) == 1
    assert 0 <= (fired[0] - hard_by).total_seconds() < 0.1


@pytest.mark.asyncio
async def test_soft_item_without_a_limit_waits_for_the_song(db_session, monkeypatch):
    fired = await _fire_times(db_session, monkeypatch, source="ad_slot", timing_mode="soft",
                              preempt_at=datetime.now(UTC) + timedelta(seconds=1))
    assert fired == []


@pytest.mark.asyncio
async def test_pages_are_told_a_minute_ahead_that_a_hard_item_is_next(db_session, monkeypatch):
    # The message sent when a 30 min story started is older than the time check's
    # entry: pages fetched the time check only when it was announced and started it
    # 1.1-1.8 s late (13:00Z, 14:00Z 2026-11-17). Pages are told again in time.
    from app.api.v1 import websocket as ws_module
    sent = []

    async def capture(station_id, payload):
        sent.append(payload)

    monkeypatch.setattr(ws_module, "broadcast_now_playing_update", capture)
    station, playing, _ = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    clip = Asset(id=uuid.uuid4(), title="Time", duration=5.0,
                 file_path="https://cdn.example.com/t.mp3", asset_type="time")
    db_session.add(clip)
    await db_session.flush()
    due = datetime.now(UTC) + timedelta(seconds=30)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=3,
                              status="pending", source="block", preempt_at=due))
    await db_session.commit()
    engine = SchedulerEngine()
    fired = []

    async def record(station_id, crossfade_seconds=0.0):
        fired.append(datetime.now(UTC))

    monkeypatch.setattr(engine, "_precise_advance", record)
    await engine._arm_timed_start(db_session, station.id)
    await engine._arm_timed_start(db_session, station.id)  # told once, not every poll
    engine._cancel_timed_timers()
    assert len(sent) == 1
    assert sent[0]["next_asset"]["id"] == str(clip.id)
    assert sent[0]["next_asset"]["cut_at"] is not None
    assert fired == []  # the exact timer waits until it is 10 s away
