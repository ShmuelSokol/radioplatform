"""One player per station.

The scheduler used to run two players on every station at once: the queue (what
the control room shows) and an older block-by-block player with its own clock,
its own now-playing record and its own broadcasts. Listeners heard the block
player's picks (old dated weather spots among them) cut into queue songs, and
the control room could show "Stopped" while the other player ran. These tests
drive the scheduler tick by tick and check that only queue items ever go out.
"""
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.now_playing import NowPlaying
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService
from app.services.scheduler_engine import SchedulerEngine


@pytest.fixture
def sent(monkeypatch):
    """Every now-playing message the scheduler broadcasts."""
    messages: list[dict] = []

    async def record(station_id, data):
        messages.append(data)

    import app.api.v1.websocket as ws

    monkeypatch.setattr(ws, "broadcast_now_playing_update", record)

    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    # The precise timer would fire later on its own; the test ticks by hand
    monkeypatch.setattr(SchedulerEngine, "_schedule_precise_advance", lambda *a, **k: None)
    return messages


async def _station(db, elapsed_seconds: float, with_pending: bool = True):
    """A station playing from its queue, with an all-day schedule block whose
    template asks for a spot, and an old dated weather spot in the library."""
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    db.add(station)
    await db.flush()
    template = PlaylistTemplate(id=uuid.uuid4(), name="Daily", station_id=station.id, is_active=True)
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Daily", is_active=True)
    db.add_all([template, schedule])
    await db.flush()
    db.add(TemplateSlot(id=uuid.uuid4(), template_id=template.id, position=0, asset_type="spot"))
    db.add(ScheduleBlock(
        id=uuid.uuid4(), schedule_id=schedule.id, name="Night Chill",
        start_time=time(0), end_time=time(23, 59, 59),
        recurrence_type=RecurrenceType.DAILY, playlist_template_id=template.id,
    ))
    old_weather = Asset(
        id=uuid.uuid4(), title="Weather Report 2026-05-18", duration=30.0,
        file_path="https://cdn.example.com/w.mp3", asset_type="spot", category="weather_spot",
    )
    songs = [
        Asset(
            id=uuid.uuid4(), title=f"Song {i}", duration=180.0,
            file_path=f"https://cdn.example.com/{i}.mp3", asset_type="music",
        )
        for i in range(3)
    ]
    db.add_all([old_weather, *songs])
    await db.flush()
    entries = [QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=songs[0].id, position=1,
        status="playing", source="auto",
        started_at=datetime.now(UTC) - timedelta(seconds=elapsed_seconds),
    )]
    if with_pending:
        entries += [
            QueueEntry(
                id=uuid.uuid4(), station_id=station.id, asset_id=s.id, position=i + 2,
                status="pending", source="auto",
            )
            for i, s in enumerate(songs[1:])
        ]
    db.add_all(entries)
    await db.commit()
    return station, songs, old_weather


async def _tick(engine: SchedulerEngine, db, station: Station):
    await engine._advance_queue(db, station.id)
    await engine._check_station(db, station)


@pytest.mark.asyncio
async def test_only_queue_items_go_on_air(db_session, sent):
    station, songs, old_weather = await _station(db_session, elapsed_seconds=10.0)
    engine = SchedulerEngine()
    for _ in range(3):
        await _tick(engine, db_session, station)
    # Mid-song: nothing changes, and the block player does not start its own item
    assert sent == []
    assert (await db_session.execute(select(NowPlaying))).scalars().all() == []

    # The song ends: the queue moves on to the next song, and only that is sent
    entry = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.status == "playing")
    )).scalar_one()
    entry.started_at = datetime.now(UTC) - timedelta(seconds=200)
    await db_session.commit()
    await _tick(engine, db_session, station)
    await _tick(engine, db_session, station)
    assert [m["asset_id"] for m in sent] == [str(songs[1].id)]
    assert str(old_weather.id) not in {m["asset_id"] for m in sent}


@pytest.mark.asyncio
async def test_leftover_block_now_playing_is_removed(db_session, sent):
    station, songs, old_weather = await _station(db_session, elapsed_seconds=10.0)
    db_session.add(NowPlaying(
        id=uuid.uuid4(), station_id=station.id, asset_id=old_weather.id,
        started_at=datetime.now(UTC), ends_at=datetime.now(UTC) + timedelta(seconds=30),
    ))
    await db_session.commit()
    await _tick(SchedulerEngine(), db_session, station)
    assert (await db_session.execute(select(NowPlaying))).scalars().all() == []
    assert sent == []


@pytest.mark.asyncio
async def test_a_station_with_nothing_on_air_starts_from_its_queue(db_session, sent):
    station, songs, old_weather = await _station(db_session, elapsed_seconds=10.0)
    playing = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.status == "playing")
    )).scalar_one()
    playing.status = "skipped"
    await db_session.commit()
    await _tick(SchedulerEngine(), db_session, station)
    now = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.status == "playing")
    )).scalar_one()
    assert now.asset_id == songs[1].id
    assert [m["asset_id"] for m in sent] == [str(songs[1].id)]


@pytest.mark.asyncio
async def test_random_fill_never_uses_dated_announcements(db_session):
    station, songs, old_weather = await _station(db_session, elapsed_seconds=10.0, with_pending=False)
    service = QueueReplenishService(db_session, station.id)
    service.max_pos = 10
    service.queued_ids = set()
    service.exclude_ids = set()
    await service._fill_content(3600, asset_type="spot", category=None)
    await db_session.commit()
    queued = {
        e.asset_id for e in (await db_session.execute(select(QueueEntry))).scalars().all()
    }
    assert old_weather.id not in queued
