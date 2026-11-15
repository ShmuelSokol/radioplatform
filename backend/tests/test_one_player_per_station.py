"""Exactly one player per station.

The queue (or the playout engine) is the only thing that plays. The scheduler's
station check used to run a second, block-based player beside it: whenever a
schedule block was active it picked songs from that block, wrote now_playing and
play_logs, and told listeners to change song, so a station ran two sequences at
once (seen on KBR Jerusalem with the "Night Chill" block).
"""
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

import app.api.v1.queue as queue_module
import app.api.v1.websocket as ws_module
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.now_playing import NowPlaying
from app.models.play_log import PlayLog
from app.models.playlist_entry import PlaylistEntry
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.services.scheduler_engine import SchedulerEngine
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def record(station_id, data):
        sent.append(data)

    monkeypatch.setattr(ws_module, "broadcast_now_playing_update", record)
    return sent


async def _with_an_all_day_block(db, station):
    """An active schedule block with its own songs, like "Night Chill"."""
    block_song = Asset(
        id=uuid.uuid4(), title="Block song", duration=200.0,
        file_path="https://cdn.example.com/block.mp3", asset_type="music",
    )
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    block = ScheduleBlock(
        id=uuid.uuid4(), schedule_id=schedule.id, name="Night Chill",
        start_time=time(0, 0), end_time=time(23, 59, 59),
        recurrence_type=RecurrenceType.DAILY,
    )
    entry = PlaylistEntry(id=uuid.uuid4(), block_id=block.id, asset_id=block_song.id, position=0)
    db.add_all([block_song, schedule, block, entry])
    await db.commit()
    return block_song


async def _count(db, model, station_id):
    return (await db.execute(
        select(func.count()).select_from(model).where(model.station_id == station_id)
    )).scalar_one()


@pytest.mark.asyncio
async def test_station_check_never_plays_beside_the_queue(db_session, broadcasts):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    block_song = await _with_an_all_day_block(db_session, station)
    engine = SchedulerEngine()

    for _ in range(3):
        await engine._check_station(db_session, station)

    assert await _count(db_session, NowPlaying, station.id) == 0
    assert await _count(db_session, PlayLog, station.id) == 0
    assert broadcasts == []
    await db_session.refresh(playing)
    assert playing.status == "playing"
    queued = (await db_session.execute(select(QueueEntry.asset_id))).scalars().all()
    assert block_song.id not in queued


@pytest.mark.asyncio
async def test_dead_air_restarts_the_queue_and_alerts_once(db_session, broadcasts):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    playing.status = "played"
    await db_session.commit()
    engine = SchedulerEngine()
    # Silent for longer than the threshold
    engine._silence_start[str(station.id)] = datetime.now(UTC) - timedelta(minutes=5)
    engine._silence_alerted.add(str(station.id))  # already alerted for this spell

    await engine._check_station(db_session, station)

    await db_session.refresh(pending)
    assert pending.status == "playing"  # recovered through the queue
    assert await _count(db_session, NowPlaying, station.id) == 0
    assert await _count(db_session, Alert, station.id) == 0  # no second alert
    assert broadcasts  # listeners were told what now plays


@pytest.mark.asyncio
async def test_a_leftover_now_playing_record_is_removed(db_session, broadcasts):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    block_song = await _with_an_all_day_block(db_session, station)
    db_session.add(NowPlaying(
        id=uuid.uuid4(), station_id=station.id, asset_id=block_song.id,
        started_at=datetime.now(UTC),
    ))
    await db_session.commit()

    await SchedulerEngine()._check_station(db_session, station)

    assert await _count(db_session, NowPlaying, station.id) == 0
    assert broadcasts == []


@pytest.mark.asyncio
async def test_start_does_not_start_a_second_item(db_session, broadcasts):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    started = await queue_module.start_from_queue(db_session, station.id)
    assert started.id == playing.id
    await db_session.refresh(pending)
    assert pending.status == "pending"
    assert broadcasts == []
