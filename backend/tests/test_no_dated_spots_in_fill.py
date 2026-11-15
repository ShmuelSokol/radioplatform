"""Generated, dated announcements (the weather for one slot, the time for one
hour) air only where they were scheduled. Random fills must never pick them:
on KBR Jerusalem an old "Weather Report 2026-05-18" aired as filler."""
import uuid

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService


async def _library(db):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    dated = [
        Asset(id=uuid.uuid4(), title="Weather Report 2026-05-18", duration=30.0,
              file_path="https://cdn.example.com/w.mp3", asset_type="spot", category="weather_spot"),
        Asset(id=uuid.uuid4(), title="Time 3 PM", duration=8.0,
              file_path="https://cdn.example.com/t.mp3", asset_type="music", category="time_announcement"),
    ]
    ok = [
        Asset(id=uuid.uuid4(), title="PSA", duration=30.0,
              file_path="https://cdn.example.com/p.mp3", asset_type="spot"),
        Asset(id=uuid.uuid4(), title="Song", duration=180.0,
              file_path="https://cdn.example.com/s.mp3", asset_type="music"),
    ]
    db.add(station)
    db.add_all(dated + ok)
    await db.commit()
    return station, {a.id for a in dated}


async def _queued(db) -> set:
    return {e.asset_id for e in (await db.execute(select(QueueEntry))).scalars().all()}


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_type", ["spot", "music"])
async def test_random_fill_skips_dated_announcements(db_session, asset_type):
    station, dated = await _library(db_session)
    service = QueueReplenishService(db_session, station.id)
    service.max_pos = 0
    service.queued_ids = set()
    service.exclude_ids = set()
    await service._fill_content(3600, asset_type=asset_type, category=None)
    await db_session.commit()
    queued = await _queued(db_session)
    assert queued and not (queued & dated)


@pytest.mark.asyncio
async def test_emergency_fallback_skips_dated_announcements(db_session, monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    station, dated = await _library(db_session)
    entry = await queue_module._refill_for_next(db_session, station.id)
    assert entry is not None
    await db_session.commit()
    assert not ((await _queued(db_session)) & dated)
