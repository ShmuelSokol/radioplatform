"""Shmuel (2026-11-17): "how do I know when it's playing from the server or not?"
/playout-status says, per station, whether listeners hear the stream server or the
built-in player, and whether the server is answering and playing what the queue says."""
import uuid
from datetime import UTC, datetime

import pytest

from app.api.v1 import playout_status
from app.config import settings
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import playout_engine


@pytest.fixture
async def engine_station(db_session, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db_session.add(station)
    song = Asset(id=uuid.uuid4(), title="Yom Shabboson", duration=240.0, file_path="s", asset_type="music")
    db_session.add(song)
    await db_session.flush()
    entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                       status="playing", source="auto", started_at=datetime.now(UTC))
    db_session.add(entry)
    await db_session.commit()
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "t")
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "https://stream.example.com/live")
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", True)
    playout_status._cache.clear()
    return station, entry


def _engine_says(monkeypatch, answer):
    async def status(station_id):
        return answer
    monkeypatch.setattr(playout_engine, "engine_status", status)


@pytest.mark.asyncio
async def test_server_station_in_step(client, engine_station, monkeypatch):
    station, entry = engine_station
    _engine_says(monkeypatch, {"ok": True, "entry_id": str(entry.id), "title": "Yom Shabboson"})
    data = (await client.get("/api/v1/playout-status")).json()
    s = data["stations"][str(station.id)]
    assert s["mode"] == "server" and s["engine_ok"] and s["in_sync"]


@pytest.mark.asyncio
async def test_server_station_out_of_step_or_down(client, engine_station, monkeypatch):
    station, _ = engine_station
    _engine_says(monkeypatch, {"ok": True, "entry_id": str(uuid.uuid4()), "title": "Another song"})
    s = (await client.get("/api/v1/playout-status")).json()["stations"][str(station.id)]
    assert s["engine_ok"] and not s["in_sync"]

    playout_status._cache.clear()
    _engine_says(monkeypatch, None)
    s = (await client.get("/api/v1/playout-status")).json()["stations"][str(station.id)]
    assert s == {**s, "mode": "server", "engine_ok": False, "in_sync": False}


@pytest.mark.asyncio
async def test_browser_player_when_stream_is_off(client, engine_station, monkeypatch):
    station, entry = engine_station
    _engine_says(monkeypatch, {"ok": True, "entry_id": str(entry.id), "title": "Yom Shabboson"})
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", False)
    s = (await client.get("/api/v1/playout-status")).json()["stations"][str(station.id)]
    assert s["mode"] == "browser"
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", False)
    data = (await client.get("/api/v1/playout-status")).json()
    assert data["stations"] == {} and data["default"] == {"mode": "browser"}
