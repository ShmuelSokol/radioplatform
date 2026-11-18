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


@pytest.mark.asyncio
async def test_an_older_song_is_never_announced_after_a_newer_one(monkeypatch):
    """Kol Bramah Main: a tick read "Taanis 26" just before the change to the next
    story and announced it 4 s after the story, so listeners jumped back."""
    sent = []

    async def fake_broadcast(station_id, message):
        sent.append(message["data"]["asset_id"])

    monkeypatch.setattr(ws.manager, "broadcast_to_station", fake_broadcast)
    station = str(uuid.uuid4())
    t0 = datetime(2026, 11, 15, 23, 50, 55, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=4, seconds=26)
    await ws.broadcast_now_playing_update(station, {"asset_id": "taanis", "started_at": t0.isoformat()})
    await ws.broadcast_now_playing_update(station, {"asset_id": "story", "started_at": t1.isoformat()})
    await ws.broadcast_now_playing_update(station, {"asset_id": "taanis", "started_at": t0.isoformat()})
    assert sent == ["taanis", "story"]
    # the same item again (a new fade length, a heartbeat) still goes out
    await ws.broadcast_now_playing_update(
        station, {"asset_id": "story", "started_at": t1.isoformat(), "change_fade_seconds": 0.5}
    )
    assert sent == ["taanis", "story", "story"]


@pytest.mark.asyncio
async def test_join_cache_does_not_keep_a_read_that_raced_a_change(monkeypatch):
    station = str(uuid.uuid4())
    ws._join_cache.clear()

    async def no_send(station_id, message):
        return None

    monkeypatch.setattr(ws.manager, "broadcast_to_station", no_send)

    async def slow_read(db, station_id):
        # the song changes (and is announced) while this read is under way
        await ws.broadcast_now_playing_update(
            station_id, {"asset_id": "new", "started_at": datetime.now(timezone.utc).isoformat()}
        )
        return {"asset_id": "old"}

    monkeypatch.setattr(ws, "_queue_now_playing", slow_read)
    await ws._cached_queue_now_playing(station, db=object())
    assert station not in ws._join_cache


@pytest.mark.asyncio
async def test_a_resumed_item_is_announced_after_the_item_that_paused_it(monkeypatch):
    """All Day Stories 02:30Z 2026-11-18: a sponsorship paused a story; the story came
    back counted as started before the sponsorship (started_at = now - offset), so every
    update for it was dropped as "older" for 25 minutes."""
    sent = []

    async def fake_broadcast(station_id, message):
        sent.append(message["data"]["asset_id"])

    monkeypatch.setattr(ws.manager, "broadcast_to_station", fake_broadcast)
    station = str(uuid.uuid4())
    story = datetime(2026, 11, 18, 2, 29, 57, tzinfo=timezone.utc)
    spot = datetime(2026, 11, 18, 2, 30, 0, tzinfo=timezone.utc)
    back = spot + timedelta(seconds=30)
    await ws.broadcast_now_playing_update(
        station, {"asset_id": "story", "started_at": story.isoformat(), "on_air_at": story.isoformat()})
    await ws.broadcast_now_playing_update(
        station, {"asset_id": "spot", "started_at": spot.isoformat(), "on_air_at": spot.isoformat()})
    # the story again, where it stopped: started_at as before, on air after the spot
    await ws.broadcast_now_playing_update(
        station, {"asset_id": "story", "started_at": story.isoformat(), "on_air_at": back.isoformat()})
    assert sent == ["story", "spot", "story"]
    # a late read of the spot is still dropped
    await ws.broadcast_now_playing_update(
        station, {"asset_id": "spot", "started_at": spot.isoformat(), "on_air_at": spot.isoformat()})
    assert sent == ["story", "spot", "story"]


@pytest.mark.asyncio
async def test_the_payload_says_when_a_resumed_item_really_went_on_air(db_session):
    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry, mark_started, resume_source
    from app.models.station import Station
    from app.services.scheduler_engine import get_scheduler

    station = Station(id=uuid.uuid4(), name="S", timezone="UTC")
    asset = Asset(id=uuid.uuid4(), title="Story", file_path="http://x/story.mp3", duration=1200.0,
                  asset_type="shiur")
    entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=1,
                       status="playing", source=resume_source(600))
    db_session.add_all([station, asset, entry])
    now = datetime(2026, 11, 18, 2, 30, 30, tzinfo=timezone.utc)
    mark_started(entry, now)
    await db_session.commit()
    await db_session.refresh(entry, ["asset"])
    payload = await get_scheduler().queue_entry_payload(db_session, station.id, entry)
    started = ws._parse_start(payload["started_at"])
    on_air = ws._parse_start(payload["on_air_at"])
    assert (on_air - started).total_seconds() == 600
