"""A station must not go silent because its queue ran out.

Next, Fade or Soft on the last item, a song ending with nothing after it, and
Start on an empty queue all fill the queue and carry on playing.
"""
import uuid

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _replenish_adds_nothing(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    queue_module._fire_requests.clear()


async def _only_one_item_left(db_session, elapsed_seconds: float = 10.0):
    """A station whose single queued item is on air, with nothing after it."""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds)
    pending.status = "played"
    await db_session.commit()
    return station, playing


@pytest.mark.asyncio
@pytest.mark.parametrize("fade", ["hard", "fade", "soft"])
async def test_next_on_the_last_item_uses_the_refill(client, auth_headers, db_session, monkeypatch, fade):
    station, playing = await _only_one_item_left(db_session)
    filler = Asset(
        id=uuid.uuid4(), title="From the schedule", duration=200.0,
        file_path="https://cdn.example.com/s.mp3", asset_type="music",
    )
    db_session.add(filler)
    await db_session.commit()

    async def replenish(db, station_id):
        db.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=filler.id,
            position=5, status="pending", source="auto",
        ))
        await db.flush()

    monkeypatch.setattr(queue_module, "_replenish_queue", replenish)
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade={fade}", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["now_playing"] == str(filler.id)
    assert body["queue"]["now_playing"]["asset_id"] == str(filler.id)


@pytest.mark.asyncio
async def test_next_on_the_last_item_falls_back_to_music(client, auth_headers, db_session):
    station, playing = await _only_one_item_left(db_session)
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade=fade", headers=auth_headers)
    body = res.json()
    assert body["now_playing"] is not None
    rows = (await db_session.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station.id, QueueEntry.status.in_(["pending", "playing"])
        )
    )).scalars().all()
    assert any(r.status == "playing" for r in rows)
    assert all(r.source == "fallback" for r in rows)


@pytest.mark.asyncio
async def test_fallback_never_uses_silence_or_spots(client, auth_headers, db_session):
    station, playing = await _only_one_item_left(db_session)
    # Only a shiur, a spot and silence besides the two tracks; take the tracks away
    for a in (await db_session.execute(select(Asset))).scalars().all():
        a.asset_type = "spot"
    shiur = Asset(
        id=uuid.uuid4(), title="Shiur", duration=1800.0,
        file_path="https://cdn.example.com/shiur.mp3", asset_type="shiur",
    )
    silence = Asset(
        id=uuid.uuid4(), title="Silence", duration=60.0,
        file_path="https://cdn.example.com/silence.mp3", asset_type="silence",
    )
    db_session.add_all([shiur, silence])
    await db_session.commit()
    body = (await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)).json()
    assert body["now_playing"] == str(shiur.id)


@pytest.mark.asyncio
async def test_song_ending_with_nothing_after_it_keeps_playing(db_session):
    station, playing = await _only_one_item_left(db_session, elapsed_seconds=200.0)
    entry = await queue_module._check_advance(db_session, station.id)
    assert entry is not None
    assert entry.status == "playing"
    assert entry.id != playing.id


@pytest.mark.asyncio
async def test_start_on_an_empty_queue_plays(client, auth_headers, db_session):
    station, playing = await _only_one_item_left(db_session)
    playing.status = "played"
    await db_session.commit()
    body = (await client.post(f"/api/v1/stations/{station.id}/queue/start", headers=auth_headers)).json()
    assert body["message"] == "Started"
    assert body["now_playing"] is not None
