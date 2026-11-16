"""The control room's "Next" line must name the item Next will really play.
On KBR Jerusalem it showed a weather report timed for later, and Next played
the song after it."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


async def _weather_ahead(db_session, preempt_at, source="auto"):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    weather = Asset(id=uuid.uuid4(), title="Weather Report 2026-11-15", duration=40.0,
                    file_path="https://cdn.example.com/w.mp3", asset_type="spot", category="weather_spot")
    db_session.add(weather)
    await db_session.flush()
    pending.position = 3
    timed = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=2,
                       status="pending", source=source, preempt_at=preempt_at)
    db_session.add(timed)
    await db_session.commit()
    return station, pending, timed


@pytest.mark.asyncio
async def test_item_timed_for_later_is_not_next(client, auth_headers, db_session):
    station, song, _ = await _weather_ahead(db_session, datetime.now(UTC) + timedelta(minutes=20))
    url = f"/api/v1/stations/{station.id}/queue"
    body = (await client.get(url, headers=auth_headers)).json()
    assert body["next_entry_id"] == str(song.id)
    played = (await client.post(f"{url}/skip?fade=fade", headers=auth_headers)).json()
    assert played["now_playing"] == str(song.asset_id)


@pytest.mark.asyncio
async def test_due_soft_slot_is_next(client, auth_headers, db_session):
    station, _, slot = await _weather_ahead(db_session, datetime.now(UTC) - timedelta(seconds=30), source="ad_slot")
    url = f"/api/v1/stations/{station.id}/queue"
    body = (await client.get(url, headers=auth_headers)).json()
    assert body["next_entry_id"] == str(slot.id)
    played = (await client.post(f"{url}/skip", headers=auth_headers)).json()
    assert played["now_playing"] == str(slot.asset_id)
