"""The top-of-hour block plays as one: time check, then weather, nothing between.

Shmuel, 2026-11-16 1 PM ET: between the time check and the weather a song played
for a split second and was cut. The weather is due at the same moment as the time
check but sits at the end of the queue, so the song (earlier by position) was
picked when the time check ended, and the weather then cut it.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


@pytest.mark.asyncio
async def test_weather_follows_the_time_check_with_no_song_between(db_session):
    from app.models.station import Station

    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db_session.add(station)
    clock = Asset(id=uuid.uuid4(), title="Time", duration=8.0, file_path="t", asset_type="jingle")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w", asset_type="jingle")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="s", asset_type="music")
    db_session.add_all([clock, weather, song])
    await db_session.flush()
    now = datetime.now(UTC)
    hour = now - timedelta(seconds=9)
    t = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock.id, position=500,
                   status="playing", preempt_at=hour, started_at=hour)
    s = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                   status="pending", source="auto")
    w = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=501,
                   status="pending", preempt_at=hour)
    db_session.add_all([t, s, w])
    await db_session.commit()

    # Up next while the time check plays is the weather
    body = await queue_module._get_queue_impl(station.id, 50, db_session)
    assert body["next_entry_id"] == str(w.id)

    # When the time check ends, the weather starts, not the song
    started = await queue_module._check_advance(db_session, station.id)
    assert started is not None and started.id == w.id
    await db_session.commit()
    song_row = (await db_session.execute(select(QueueEntry).where(QueueEntry.id == s.id))).scalar_one()
    assert song_row.status == "pending" and song_row.started_at is None


@pytest.mark.asyncio
async def test_a_time_check_queued_twice_plays_once(db_session):
    """Main had the 1 PM time check twice (two refills racing); both played."""
    from app.models.station import Station

    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db_session.add(station)
    clock = Asset(id=uuid.uuid4(), title="Time", duration=5.0, file_path="t", asset_type="jingle")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w", asset_type="jingle")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="s", asset_type="music")
    db_session.add_all([clock, weather, song])
    await db_session.flush()
    now = datetime.now(UTC)
    hour = now - timedelta(seconds=1)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                              status="playing", source="auto", started_at=now - timedelta(seconds=60)))
    first = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock.id, position=90,
                       status="pending", preempt_at=hour, timing_mode="hard")
    twin = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock.id, position=91,
                      status="pending", preempt_at=hour, timing_mode="hard")
    w = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=92,
                   status="pending", preempt_at=hour, timing_mode="hard")
    db_session.add_all([first, twin, w])
    await db_session.commit()
    twin_id, weather_id = twin.id, w.id

    started = await queue_module._check_advance(db_session, station.id)
    assert started.id == first.id
    await db_session.commit()
    twin_row = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.id == twin_id).execution_options(populate_existing=True)
    )).scalar_one()
    assert twin_row.status == "skipped"
    body = await queue_module._get_queue_impl(station.id, 50, db_session)
    assert body["next_entry_id"] == str(weather_id)
