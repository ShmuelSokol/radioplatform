"""A listener shows in the live count the moment they join and goes the moment they leave."""
import asyncio
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import listeners as listeners_api
from app.api.v1 import playlists as playlists_api
from app.models.station import Station


@pytest.mark.asyncio
async def test_join_and_leave_change_the_live_count_at_once(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    pings: list[int] = []

    async def fake_notify():
        pings.append(1)

    monkeypatch.setattr(listeners_api, "_notify_watchers", fake_notify)
    async def no_geo(ip):
        return {"country": "Local", "region": "Local", "city": "Local"}

    monkeypatch.setattr(listeners_api, "_resolve_geo", no_geo)

    station = Station(id=uuid.uuid4(), name="KBR live count", timezone="America/New_York", is_active=True)
    db_session.add(station)
    await db_session.commit()
    body = {"station_id": str(station.id), "session_key": "ls_test_1"}

    async def live() -> int:
        r = await client.get("/api/v1/listeners/live", headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()["total_listeners"]

    r = await client.post("/api/v1/listeners/heartbeat", json=body)
    assert r.status_code == 200, r.text
    assert await live() == 1
    assert len(pings) == 1  # join announced

    await client.post("/api/v1/listeners/heartbeat", json=body)
    assert len(pings) == 1  # an ordinary heartbeat changes nothing

    # Closing the page: a text/plain beacon
    r = await client.post(
        "/api/v1/listeners/leave", content=json.dumps(body), headers={"content-type": "text/plain"}
    )
    assert r.status_code == 200, r.text
    assert await live() == 0
    assert len(pings) == 2

    # Coming back counts again
    await client.post("/api/v1/listeners/heartbeat", json=body)
    assert await live() == 1
    assert len(pings) == 3

    await client.post("/api/v1/listeners/disconnect", json=body)
    assert await live() == 0
    assert len(pings) == 4

    r = await client.post("/api/v1/listeners/leave", content=b"not json")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_quick_playlist_edits_share_one_background_rebuild(monkeypatch):
    calls: list = []

    async def fake_refresh(db, station_id, *, had_playlists=False):
        calls.append(station_id)

    class FakeSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    import app.db.engine as engine_mod
    import app.services.schedule_refresh_service as srs

    monkeypatch.setattr(srs, "refresh_station", fake_refresh)
    monkeypatch.setattr(engine_mod, "async_session_factory", lambda: FakeSession())
    monkeypatch.setattr(playlists_api, "REFRESH_DELAY", 0.05)

    station = uuid.uuid4()
    for _ in range(4):  # a reorder sends one save per moved step
        playlists_api._schedule_refresh(station)
    await asyncio.sleep(0.2)
    assert calls == [station]

    playlists_api._schedule_refresh(station)  # a later edit gets its own rebuild
    await asyncio.sleep(0.2)
    assert calls == [station, station]
