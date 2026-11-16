"""Pressing Next many times in a row never loses a timed item.

Shmuel, 2026-11-16 1:35 PM: pressing Next repeatedly on KBR Jerusalem skipped the
weather a second after it started; the played list showed it, but it was not heard.
Next never skips a timed item (time check, weather, sponsorship): it plays to the
end. Songs stay skippable at once.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    async def no_broadcast(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", no_broadcast)
    queue_module._automatic_change.clear()
    yield
    queue_module._automatic_change.clear()


async def _station(db, *, weather_timing="hard"):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db.add(station)
    songs = [Asset(id=uuid.uuid4(), title=f"Song {i}", duration=200.0, file_path=f"s{i}",
                   asset_type="music") for i in range(12)]
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w", asset_type="spot")
    db.add_all([*songs, weather])
    await db.flush()
    now = datetime.now(UTC)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=songs[0].id, position=1,
                      status="playing", source="auto", started_at=now - timedelta(seconds=30)))
    for i, song in enumerate(songs[1:], start=2):
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=i,
                          status="pending", source="auto"))
    w = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=90,
                   status="pending", preempt_at=now - timedelta(seconds=5), timing_mode=weather_timing)
    db.add(w)
    await db.commit()
    return station, w.id


async def _row(db, entry_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.id == entry_id).execution_options(populate_existing=True)
    )).scalar_one()


@pytest.mark.parametrize("timing", ["hard", "soft"])
@pytest.mark.asyncio
async def test_many_nexts_in_a_row_keep_the_weather_on(client, auth_headers, db_session, timing):
    station, weather_id = await _station(db_session, weather_timing=timing)
    url = f"/api/v1/stations/{station.id}/queue/skip"

    # First press: the weather, already due, goes on air
    body = (await client.post(url, headers=auth_headers)).json()
    assert body["message"] == "Skipped"
    w = await _row(db_session, weather_id)
    assert w.status == "playing" and w.started_at is not None

    # Pressed again and again straight away: the weather keeps playing
    for _ in range(8):
        body = (await client.post(url, headers=auth_headers)).json()
        assert body.get("kept_incoming") is True and body.get("protected") is True
        assert (await _row(db_session, weather_id)).status == "playing"


@pytest.mark.asyncio
async def test_songs_can_still_be_skipped_quickly(client, auth_headers, db_session):
    station, weather_id = await _station(db_session)
    url = f"/api/v1/stations/{station.id}/queue/skip"
    await client.post(url, headers=auth_headers)  # the weather goes on air
    # Even much later, Next leaves it on; it ends by itself
    queue_module._automatic_change.clear()
    assert (await client.post(url, headers=auth_headers)).json().get("protected") is True
    w = await _row(db_session, weather_id)
    w.started_at = datetime.now(UTC) - timedelta(seconds=41)
    await db_session.commit()
    await queue_module._check_advance(db_session, station.id)
    await db_session.commit()
    assert (await _row(db_session, weather_id)).status == "played"
    queue_module._automatic_change.clear()
    seen = set()
    for _ in range(6):  # then several songs in quick succession, one per press
        body = (await client.post(url, headers=auth_headers)).json()
        assert body["message"] == "Skipped" and not body.get("kept_incoming")
        seen.add(body["now_playing"])
    assert len(seen) == 6
    # Nothing timed was marked done without having been on air
    rows = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status.in_(["played", "skipped"]),
    ).execution_options(populate_existing=True))).scalars().all()
    assert all(r.started_at is not None for r in rows)
