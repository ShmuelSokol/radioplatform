"""Playout engine link: the engine asks what plays next and reports real starts."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import playout_engine

TOKEN = "test-playout-token"
HEADERS = {"X-Playout-Token": TOKEN}


@pytest_asyncio.fixture
async def engine_station(db_session, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Engine FM", timezone="America/New_York")
    db_session.add(station)
    assets = []
    for i in range(4):
        asset = Asset(
            id=uuid.uuid4(),
            title=f'Song "{i}"',
            artist="Artist",
            duration=180.0,
            file_path=f"https://cdn.example.com/audio/{i}.mp3",
            asset_type="music",
        )
        db_session.add(asset)
        assets.append(asset)
    await db_session.flush()
    entries = []
    for i, asset in enumerate(assets):
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=station.id,
            asset_id=asset.id,
            position=i + 1,
            status="pending",
            source="auto",
        )
        db_session.add(entry)
        entries.append(entry)
    await db_session.commit()

    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    playout_engine.reset_state()

    sent = []

    async def fake_command(path, payload=None):
        sent.append((path, payload))
        return True

    async def no_replenish(db, station_id):
        return None

    async def no_broadcast(db, station_id, entry_id):
        return None

    monkeypatch.setattr(playout_engine, "_command", fake_command)
    import app.api.v1.queue as queue_module

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", no_broadcast)
    return station, entries, sent


async def _status(db_session, entry):
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entry.id))
    row = res.scalar_one()
    await db_session.refresh(row)
    return row.status


@pytest.mark.asyncio
async def test_requires_token(client, engine_station):
    station, _, _ = engine_station
    assert (await client.get(f"/api/v1/playout/{station.id}/next")).status_code == 403
    bad = await client.get(
        f"/api/v1/playout/{station.id}/next", headers={"X-Playout-Token": "nope"}
    )
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_other_station_is_not_engine_driven(client, engine_station):
    resp = await client.get(f"/api/v1/playout/{uuid.uuid4()}/next", headers=HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_next_then_started_advances_queue(client, db_session, engine_station):
    station, entries, _ = engine_station

    first = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert first.status_code == 200
    body = first.json()
    assert body["entry_id"] == str(entries[0].id)
    assert body["uri"].startswith("annotate:")
    assert body["uri"].endswith(":https://cdn.example.com/audio/0.mp3")
    assert f'kbr_entry="{entries[0].id}"' in body["uri"]
    assert 'title="Song \\"0\\""' in body["uri"]  # quotes escaped

    # Asking again before the start report must not hand out the same entry twice
    second = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert second.json()["entry_id"] == str(entries[1].id)

    r = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    assert r.json() == {"ok": True, "known": True}
    assert await _status(db_session, entries[0]) == "playing"

    r = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[1].id), "lane": "main"},
    )
    assert r.status_code == 200
    assert await _status(db_session, entries[0]) == "played"
    assert await _status(db_session, entries[1]) == "playing"


@pytest.mark.asyncio
async def test_skip_goes_to_engine_and_logs_manual(
    client, db_session, auth_headers, engine_station
):
    station, entries, sent = engine_station
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    # Pretend it has been on air for a while
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[0].id))
    row = res.scalar_one()
    row.started_at = datetime.now(UTC) - timedelta(seconds=30)
    await db_session.commit()

    resp = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert resp.status_code == 200
    assert ("/skip", None) in sent
    # The database only changes when the engine reports the real start
    assert await _status(db_session, entries[0]) == "playing"

    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[1].id), "lane": "main"},
    )
    assert await _status(db_session, entries[0]) == "skipped"
    logs = (
        (await db_session.execute(select(PlayLog).where(PlayLog.station_id == station.id)))
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].asset_id == entries[0].asset_id
    assert str(getattr(logs[0].source, "value", logs[0].source)) == "manual"


@pytest.mark.asyncio
async def test_skip_reports_engine_down(client, auth_headers, engine_station, monkeypatch):
    station, _, _ = engine_station

    async def down(path, payload=None):
        return False

    monkeypatch.setattr(playout_engine, "_command", down)
    resp = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_tick_flushes_when_queue_head_changes(client, db_session, engine_station):
    station, entries, sent = engine_station
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)  # entries[0] pre-loaded

    await playout_engine.tick(db_session, station)
    assert ("/flush", None) not in sent  # nothing changed

    # Operator moves another entry to the front
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[3].id))
    res.scalar_one().position = 0
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    assert ("/flush", None) in sent
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[3].id)


@pytest.mark.asyncio
async def test_tick_hands_over_timed_entry_once(client, db_session, engine_station):
    station, entries, sent = engine_station
    air_time = datetime.now(UTC) + timedelta(seconds=20)
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[2].id))
    timed = res.scalar_one()
    timed.preempt_at = air_time
    timed.source = "auto"
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    await playout_engine.tick(db_session, station)
    timed_calls = [p for path, p in sent if path == "/timed"]
    assert len(timed_calls) == 1
    assert abs(timed_calls[0]["at"] - air_time.timestamp()) < 0.01
    assert 'kbr_lane="timed"' in timed_calls[0]["uri"]

    # The main lane never hands out an entry that went to the timed lane
    handed = []
    for _ in range(3):
        handed.append(
            (await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)).json()[
                "entry_id"
            ]
        )
    assert str(entries[2].id) not in handed


@pytest.mark.asyncio
async def test_emergency_report_closes_current_entry(client, db_session, engine_station):
    station, entries, _ = engine_station
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    r = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": "", "lane": "emergency"},
    )
    assert r.json() == {"ok": True, "known": False}
    assert await _status(db_session, entries[0]) == "played"


@pytest.mark.asyncio
async def test_stream_url_only_when_live(engine_station, monkeypatch):
    station, _, _ = engine_station
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "https://stream.example.com/live")
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", False)
    assert settings.public_stream_url(station.id) is None
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", True)
    assert settings.public_stream_url(station.id) == "https://stream.example.com/live"
    assert settings.public_stream_url(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_fire_now_uses_timed_lane(client, db_session, auth_headers, engine_station):
    station, entries, sent = engine_station
    asset_id = str(entries[3].asset_id)
    resp = await client.post(
        f"/api/v1/stations/{station.id}/queue/fire-now",
        headers=auth_headers,
        json={"asset_id": asset_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["on_air"] is True
    timed_calls = [p for path, p in sent if path == "/timed"]
    assert len(timed_calls) == 1
    assert f'kbr_entry="{body["id"]}"' in timed_calls[0]["uri"]
    # The main lane must not also play it
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] != body["id"]


@pytest.mark.asyncio
async def test_fire_now_after_current_only_queues(client, auth_headers, engine_station):
    station, entries, sent = engine_station
    resp = await client.post(
        f"/api/v1/stations/{station.id}/queue/fire-now",
        headers=auth_headers,
        json={"asset_id": str(entries[3].asset_id), "mode": "after-current"},
    )
    assert resp.status_code == 201
    assert resp.json()["on_air"] is False
    assert not [p for path, p in sent if path == "/timed"]


@pytest.mark.asyncio
async def test_next_catches_up_on_lost_start_report(client, db_session, engine_station):
    station, entries, _ = engine_station
    # The engine played entries[0] while we were unreachable: no start report arrived.
    playout_engine.reset_state()
    resp = await client.get(
        f"/api/v1/playout/{station.id}/next?on_air={entries[0].id}", headers=HEADERS
    )
    assert resp.json()["entry_id"] == str(entries[1].id)
    assert await _status(db_session, entries[0]) == "played"
