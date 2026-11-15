"""Listeners get a now-playing message when something changes, not every tick, and
a listener who joins (or polls) mid-song is told the song the queue is playing."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import websocket as ws
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.mark.asyncio
async def test_unchanged_message_is_not_resent(monkeypatch):
    sent = []

    async def fake_broadcast(station_id, message):
        sent.append(message)

    monkeypatch.setattr(ws.manager, "broadcast_to_station", fake_broadcast)
    ws._last_now_playing.clear()
    station = str(uuid.uuid4())
    song = {"asset_id": "a", "started_at": "t1", "next_asset": {"id": "b"}}
    await ws.broadcast_now_playing_update(station, song)
    await ws.broadcast_now_playing_update(station, dict(song))  # scheduler tick, nothing new
    assert len(sent) == 1
    # the next song, or a different next track, goes out at once
    await ws.broadcast_now_playing_update(station, {**song, "next_asset": {"id": "c"}})
    await ws.broadcast_now_playing_update(station, {**song, "asset_id": "b", "started_at": "t2"})
    assert len(sent) == 3
    # an unchanged message still goes out as a heartbeat now and then
    key, _ = ws._last_now_playing[station]
    ws._last_now_playing[station] = (key, 0.0)
    await ws.broadcast_now_playing_update(station, {**song, "asset_id": "b", "started_at": "t2"})
    assert len(sent) == 4


@pytest.mark.asyncio
async def test_joining_listener_gets_the_queue_song(client: AsyncClient, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Queue station", timezone="UTC")
    playing = Asset(id=uuid.uuid4(), title="On air now", artist="A", duration=200, file_path="x.mp3", asset_type="music")
    after = Asset(id=uuid.uuid4(), title="Up next", artist="B", duration=200, file_path="y.mp3", asset_type="music")
    db_session.add_all([station, playing, after])
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=playing.id, position=1,
                   status="playing", source="auto", started_at=started),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=2,
                   status="pending", source="auto"),
    ])
    await db_session.commit()

    payload = await ws._queue_now_playing(db_session, str(station.id))
    assert payload["asset_id"] == str(playing.id)
    assert payload["asset"]["title"] == "On air now"
    assert payload["next_asset"]["title"] == "Up next"

    # the polling fallback answers with the same message
    ws._join_cache.clear()
    r = await client.get(f"/api/v1/now-playing/{station.id}")
    assert r.status_code == 200
    assert r.json()["asset_id"] == str(playing.id)
    assert r.json()["next_asset"]["title"] == "Up next"
