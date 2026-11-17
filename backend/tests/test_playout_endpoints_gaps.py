"""Playout endpoints rows that had no test: hello resets the engine's state, empty
queue refills once (never during a blackout), unplayable entries are dropped, and a
start report broadcasts and refills (not during a blackout). B-PLAYOUT-1, 2, 6, 9, 15."""

import pytest
from sqlalchemy import update

import app.api.v1.queue as queue_module
from app.models.queue_entry import QueueEntry
from app.services import playout_engine
from tests import test_playout_engine as _pe
from tests.test_playout_engine import HEADERS, _status

engine_station = _pe.engine_station  # the engine fixture


def _calls(monkeypatch, blacked_out=False):
    seen = {"replenish": 0, "broadcast": []}

    async def replenish(db, station_id):
        seen["replenish"] += 1

    async def broadcast(db, station_id, entry_id):
        seen["broadcast"].append(str(entry_id))

    async def blackout(db, station_id):
        return blacked_out

    monkeypatch.setattr(queue_module, "_replenish_queue", replenish)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", broadcast)
    monkeypatch.setattr(queue_module, "_is_blacked_out", blackout)
    return seen


async def _empty(db_session, station):
    await db_session.execute(update(QueueEntry).where(QueueEntry.station_id == station.id)
                             .values(status="played"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_hello_resets_what_the_engine_was_given(client, engine_station):
    station, entries, _ = engine_station
    state = playout_engine.get_state(station.id)
    state.cued_entry_id = str(entries[0].id)
    state.timed_sent.add("old-timed")
    r = await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS, json={"proto": 2})
    assert r.status_code == 200 and r.json() == {"ok": True}
    state = playout_engine.get_state(station.id)
    assert state.cued_entry_id is None
    assert not state.timed_sent
    assert state.engine_proto == 2
    # An old engine sends no body: proto 0
    r = await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS)
    assert r.status_code == 200
    assert playout_engine.get_state(station.id).engine_proto == 0
    # After a restart the first entry is handed out again (the engine holds nothing)
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[0].id)


@pytest.mark.asyncio
async def test_hello_needs_the_token(client, engine_station):
    station, _, _ = engine_station
    assert (await client.post(f"/api/v1/playout/{station.id}/hello")).status_code == 403


@pytest.mark.asyncio
async def test_empty_queue_refills_once_then_204(client, db_session, engine_station, monkeypatch):
    station, _, _ = engine_station
    seen = _calls(monkeypatch)
    await _empty(db_session, station)
    r = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert r.status_code == 204
    assert seen["replenish"] == 1


@pytest.mark.asyncio
async def test_empty_queue_is_not_refilled_during_a_blackout(client, db_session, engine_station, monkeypatch):
    station, _, _ = engine_station
    seen = _calls(monkeypatch, blacked_out=True)
    await _empty(db_session, station)
    r = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert r.status_code == 204
    assert seen["replenish"] == 0


@pytest.mark.asyncio
async def test_entry_without_a_playable_file_is_dropped_and_the_next_handed(
    client, db_session, engine_station, monkeypatch
):
    station, entries, _ = engine_station
    _calls(monkeypatch)
    real = playout_engine.build_uri

    def no_uri_for_first(entry, lane):
        return None if entry.id == entries[0].id else real(entry, lane)

    monkeypatch.setattr(playout_engine, "build_uri", no_uri_for_first)
    r = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert r.json()["entry_id"] == str(entries[1].id)
    assert await _status(db_session, entries[0]) == "skipped"


@pytest.mark.asyncio
async def test_nothing_playable_raises_an_alert(client, db_session, engine_station, monkeypatch):
    station, entries, _ = engine_station
    _calls(monkeypatch)
    alerts = []

    async def alert(db, station_id, state, title, message):
        alerts.append(title)

    monkeypatch.setattr(playout_engine, "build_uri", lambda entry, lane: None)
    monkeypatch.setattr(playout_engine, "_alert", alert)
    r = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert r.status_code == 204
    assert alerts == ["Playout engine has nothing it can play"]
    for e in entries:
        assert await _status(db_session, e) == "skipped"


@pytest.mark.asyncio
async def test_start_report_broadcasts_and_refills(client, engine_station, monkeypatch):
    station, entries, _ = engine_station
    seen = _calls(monkeypatch)
    r = await client.post(f"/api/v1/playout/{station.id}/started", headers=HEADERS,
                          json={"entry_id": str(entries[0].id), "lane": "main"})
    assert r.status_code == 200
    assert seen["broadcast"] == [str(entries[0].id)]
    assert seen["replenish"] == 1


@pytest.mark.asyncio
async def test_start_report_does_not_refill_during_a_blackout(client, engine_station, monkeypatch):
    station, entries, _ = engine_station
    seen = _calls(monkeypatch, blacked_out=True)
    r = await client.post(f"/api/v1/playout/{station.id}/started", headers=HEADERS,
                          json={"entry_id": str(entries[0].id), "lane": "main"})
    assert r.status_code == 200
    assert seen["broadcast"] == [str(entries[0].id)]
    assert seen["replenish"] == 0


# ── Operator actions on an engine station (B-QUEUE-23, B-QUEUE-41, B-MODE-5/7/8) ──


@pytest.mark.asyncio
async def test_mix_over_goes_to_the_engine_overlay_lane(client, auth_headers, db_session, engine_station):
    station, entries, sent = engine_station
    asset_id = entries[2].asset_id
    before = [(await _status(db_session, e)) for e in entries]
    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(asset_id)})
    assert r.status_code == 200
    overlays = [p for path, p in sent if path == "/overlay"]
    assert len(overlays) == 1
    assert overlays[0]["uri"].startswith("annotate:kbr_lane=")
    assert "overlay" in overlays[0]["uri"]
    assert overlays[0]["uri"].endswith(":https://cdn.example.com/audio/2.mp3")
    # The queue does not move
    assert [(await _status(db_session, e)) for e in entries] == before


@pytest.mark.asyncio
async def test_mix_over_engine_down_is_503(client, auth_headers, engine_station, monkeypatch):
    station, entries, _ = engine_station

    async def down(path, payload=None, **_kw):
        return False

    monkeypatch.setattr(playout_engine, "_command", down)
    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(entries[1].asset_id)})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_insert_flushes_the_engine_so_it_asks_again(client, auth_headers, engine_station):
    station, entries, sent = engine_station
    playout_engine.get_state(station.id).cued_entry_id = str(entries[0].id)
    r = await client.post(f"/api/v1/stations/{station.id}/queue/insert", headers=auth_headers,
                          json={"asset_ids": [str(entries[3].asset_id)]})
    assert r.status_code in (200, 201), r.text
    assert "/flush" in [path for path, _ in sent]
    assert playout_engine.get_state(station.id).cued_entry_id is None


@pytest.mark.asyncio
async def test_start_on_an_engine_station_leaves_it_to_the_engine(
    client, auth_headers, db_session, engine_station, monkeypatch
):
    station, entries, _ = engine_station
    seen = _calls(monkeypatch)
    r = await client.post(f"/api/v1/stations/{station.id}/queue/start", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"message": "Playout engine will start the next track", "now_playing": None}
    # Nothing is marked on air until the engine reports a real start
    assert [(await _status(db_session, e)) for e in entries] == ["pending"] * 4
    assert seen["replenish"] == 1
