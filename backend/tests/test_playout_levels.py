"""The engine's on-air level reaches the station's pages for the control room meters
(POST /playout/{station}/levels, wiki/control-room.md "Level meters")."""

import asyncio
import json

import pytest

from app.api.v1 import websocket as ws_module
from tests import test_playout_engine as _pe
from tests.test_playout_engine import HEADERS

engine_station = _pe.engine_station  # the engine fixture


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.mark.asyncio
async def test_levels_go_to_the_stations_pages(client, engine_station):
    station, _, _ = engine_station
    sock, other = FakeSocket(), FakeSocket()
    ws_module.manager.active_connections[str(station.id)] = {sock}
    ws_module.manager.active_connections["another-station"] = {other}
    try:
        r = await client.post(f"/api/v1/playout/{station.id}/levels", headers=HEADERS,
                              json={"l": [0.1, 0.25, 2.0, -1, 0.3], "r": [0.2] * 5, "step": 0.1, "at": 1.0})
        assert r.status_code == 200 and r.json() == {"ok": True}
        for _ in range(20):
            if sock.sent:
                break
            await asyncio.sleep(0.01)
        assert sock.sent == [{"type": "levels", "data": {"l": [0.1, 0.25, 1.0, 0.0, 0.3], "r": [0.2] * 5, "step": 0.1}}]
        assert other.sent == []  # one station's level never shows on another
    finally:
        ws_module.manager.active_connections.pop(str(station.id), None)
        ws_module.manager.active_connections.pop("another-station", None)


@pytest.mark.asyncio
async def test_levels_need_the_token(client, engine_station):
    station, _, _ = engine_station
    r = await client.post(f"/api/v1/playout/{station.id}/levels", json={"l": [0.1], "r": [0.1]})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_levels_with_no_page_open_send_nothing(client, engine_station):
    station, _, _ = engine_station
    ws_module.manager.active_connections.pop(str(station.id), None)
    r = await client.post(f"/api/v1/playout/{station.id}/levels", headers=HEADERS, json={"l": [0.5] * 10, "r": [0.5] * 10})
    assert r.status_code == 200
    assert not ws_module._level_tasks
