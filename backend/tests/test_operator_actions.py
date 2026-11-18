"""Operator actions: Next with a fade choice, insert at a point, fire-now double tap, intro length."""
import pytest

import app.api.v1.queue as queue_module
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    queue_module._fire_requests.clear()


@pytest.mark.asyncio
async def test_skip_reports_the_fade_for_each_button(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    url = f"/api/v1/stations/{station.id}/queue/skip"
    body = (await client.post(f"{url}?fade=soft", headers=auth_headers)).json()
    assert body["fade_seconds"] == 3.0
    assert body["now_playing"] == str(pending.asset_id)
    assert (await client.post(f"{url}?fade=hard", headers=auth_headers)).json()["fade_seconds"] == 0.0
    assert (await client.post(url, headers=auth_headers)).json()["fade_seconds"] == 0.5
    assert (await client.post(f"{url}?fade=slow", headers=auth_headers)).status_code == 422


@pytest.mark.asyncio
async def test_insert_several_after_an_entry_keeps_their_order(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    first, second = str(playing.asset_id), str(pending.asset_id)
    url = f"/api/v1/stations/{station.id}/queue/insert"

    # After the playing entry = at the top, ahead of what was next
    res = await client.post(
        url, headers=auth_headers,
        json={"asset_ids": [first, second], "after_entry_id": str(playing.id)},
    )
    assert res.status_code == 201
    body = res.json()
    rows = body["queue"]["entries"]
    upcoming = [r for r in rows if r["status"] == "pending"]
    assert [r["id"] for r in upcoming[:2]] == body["ids"]
    assert [r["asset_id"] for r in upcoming] == [first, second, second]
    assert [r["position"] for r in upcoming] == sorted({r["position"] for r in upcoming})

    # No anchor = top as well; a single asset_id is accepted
    body = (await client.post(url, headers=auth_headers, json={"asset_id": first})).json()
    upcoming = [r for r in body["queue"]["entries"] if r["status"] == "pending"]
    assert upcoming[0]["id"] == body["ids"][0]

    # After the last entry = at the end
    last = upcoming[-1]["id"]
    body = (
        await client.post(url, headers=auth_headers, json={"asset_id": first, "after_entry_id": last})
    ).json()
    upcoming = [r for r in body["queue"]["entries"] if r["status"] == "pending"]
    assert upcoming[-1]["id"] == body["ids"][0]

    assert (await client.post(url, headers=auth_headers, json={})).status_code == 422


@pytest.mark.asyncio
async def test_fire_now_double_tap_fires_once(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    url = f"/api/v1/stations/{station.id}/queue/fire-now"
    payload = {"asset_id": str(playing.asset_id), "client_request_id": "tap-1"}
    one = (await client.post(url, headers=auth_headers, json=payload)).json()
    two = (await client.post(url, headers=auth_headers, json=payload)).json()
    assert one["on_air"] is True and one["id"] == two["id"]
    assert two.get("duplicate") is True
    queue = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert queue["now_playing"]["id"] == one["id"]
    # Only the original next track is still waiting: nothing was queued twice
    assert [r["asset_id"] for r in queue["entries"] if r["status"] == "pending"] == [
        str(pending.asset_id)
    ]
    # A new tap is a new request
    three = (
        await client.post(url, headers=auth_headers, json={**payload, "client_request_id": "tap-2"})
    ).json()
    assert three["id"] != one["id"]


@pytest.mark.asyncio
async def test_fire_now_goes_on_air_even_during_an_automatic_change(
    client, auth_headers, db_session
):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=178.0)
    fired_asset = str(playing.asset_id)
    await queue_module._check_advance(db_session, station.id, early_seconds=3.0)
    url = f"/api/v1/stations/{station.id}/queue/fire-now"
    body = (await client.post(url, headers=auth_headers, json={"asset_id": fired_asset})).json()
    assert body["queue"]["now_playing"]["id"] == body["id"]


@pytest.mark.asyncio
async def test_intro_length_is_saved_and_shown_in_the_queue(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    res = await client.patch(
        f"/api/v1/assets/{pending.asset_id}", headers=auth_headers, json={"intro_seconds": 12.5}
    )
    assert res.status_code == 200 and res.json()["intro_seconds"] == 12.5
    queue = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    row = next(r for r in queue["entries"] if r["id"] == str(pending.id))
    assert row["asset"]["intro_seconds"] == 12.5
    assert "intro_seconds" in queue["now_playing"]["asset"]


async def _finish_current(db_session, station):
    """Make the item on air reach its end, then let the station advance."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.queue_entry import QueueEntry

    res = await db_session.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station.id, QueueEntry.status == "playing"
        ).execution_options(populate_existing=True)
    )
    current = res.scalar_one()
    current.started_at = datetime.now(UTC) - timedelta(seconds=181)
    await db_session.commit()
    entry = await queue_module._check_advance(db_session, station.id)
    return entry


@pytest.mark.asyncio
async def test_loop_repeats_the_item_on_air_until_switched_off(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    looped_asset, other_asset, other_entry = playing.asset_id, pending.asset_id, pending.id
    url = f"/api/v1/stations/{station.id}/queue/loop"

    body = (await client.post(url, headers=auth_headers, json={"on": True})).json()
    assert body["loop"] is True and body["asset_id"] == str(looped_asset)
    assert body["queue"]["loop_asset_id"] == str(looped_asset)

    # It ends twice: both times the same item starts again, the next song keeps waiting
    for _ in range(2):
        entry = await _finish_current(db_session, station)
        assert entry.asset_id == looped_asset and entry.source == "loop"

    # Loop off: the play in progress finishes, then the queue carries on
    body = (await client.post(url, headers=auth_headers, json={"on": False})).json()
    assert body["loop"] is False and body["queue"]["loop_asset_id"] is None
    entry = await _finish_current(db_session, station)
    assert entry.id == other_entry and entry.asset_id == other_asset


@pytest.mark.asyncio
async def test_next_ends_a_loop(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    other_asset = str(pending.asset_id)
    base = f"/api/v1/stations/{station.id}/queue"
    await client.post(f"{base}/loop", headers=auth_headers, json={"on": True})
    await _finish_current(db_session, station)  # one repeat is on air now
    queue_module._automatic_change.clear()  # the change is long over when Next is pressed

    body = (await client.post(f"{base}/skip", headers=auth_headers)).json()
    assert body["now_playing"] == other_asset
    assert body["queue"]["loop_asset_id"] is None
    assert not [r for r in body["queue"]["entries"] if r["source"] == "loop" and r["status"] == "pending"]


@pytest.mark.asyncio
async def test_mix_over_plays_on_top_and_leaves_the_queue_alone(
    client, auth_headers, db_session, monkeypatch
):
    from sqlalchemy import select

    from app.api.v1 import websocket as ws_module
    from app.models.asset import Asset
    from app.models.play_log import PlayLog
    from app.services import playout_engine

    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    jingle = await db_session.get(Asset, pending.asset_id)
    jingle.file_path = "https://files.example/jingle.mp3"
    await db_session.commit()
    sent = []

    async def fake_broadcast(station_id, message):
        sent.append((station_id, message))

    monkeypatch.setattr(ws_module.manager, "broadcast_to_station", fake_broadcast)
    url = f"/api/v1/stations/{station.id}/queue/mix-over"
    before = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()

    res = await client.post(
        url, headers=auth_headers, json={"asset_id": str(jingle.id), "client_request_id": "tap-1"}
    )
    assert res.status_code == 200 and res.json()["on_air"] is True
    # Browser playback: every open player is told to mix it in
    assert len(sent) == 1 and sent[0][1]["type"] == "mix_over"
    assert sent[0][1]["data"]["url"] == "https://files.example/jingle.mp3"
    # A double tap plays it once
    again = await client.post(
        url, headers=auth_headers, json={"asset_id": str(jingle.id), "client_request_id": "tap-1"}
    )
    assert again.json().get("duplicate") is True and len(sent) == 1

    after = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert [(r["id"], r["status"]) for r in after["entries"]] == [
        (r["id"], r["status"]) for r in before["entries"]
    ]
    logs = (await db_session.execute(select(PlayLog).where(PlayLog.asset_id == jingle.id))).scalars().all()
    assert len(logs) == 1

    # On an engine station the engine gets it instead, as an overlay
    commands = []

    async def fake_command(path, payload=None, **_kw):
        commands.append((path, payload))
        return True

    monkeypatch.setattr(playout_engine, "_command", fake_command)
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_TOKEN", "test-token")
    res = await client.post(url, headers=auth_headers, json={"asset_id": str(jingle.id)})
    assert res.status_code == 200 and len(sent) == 1
    assert commands[0][0] == "/overlay" and commands[0][1]["uri"].endswith(":https://files.example/jingle.mp3")
    assert 'kbr_lane="overlay"' in commands[0][1]["uri"]


async def _engine_station(db_session, monkeypatch, elapsed_seconds=20.0):
    """A station on the playout engine that already holds its next (automatic) song."""
    from app.services import playout_engine

    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=elapsed_seconds)
    commands = []

    async def fake_command(path, payload=None, **_kw):
        commands.append(path)
        return True

    monkeypatch.setattr(playout_engine, "_command", fake_command)
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(queue_module.settings, "PLAYOUT_ENGINE_TOKEN", "test-token")
    monkeypatch.setattr(playout_engine, "OPERATOR_ASK_WAIT", 0.1)
    monkeypatch.setattr(playout_engine, "OPERATOR_LOAD_SECONDS", 0.0)
    playout_engine._states.pop(str(station.id), None)
    state = playout_engine.get_state(station.id)
    state.cued_entry_id = str(pending.id)
    state.given = {str(pending.id)}
    return station, playing, pending, state, commands


@pytest.mark.asyncio
async def test_play_next_takes_back_the_song_the_engine_holds(client, auth_headers, db_session, monkeypatch):
    """Harness operator_queue_then_next, 2026-11-18: Play next then Next played the
    automatic song the engine already held; the operator's item came one Next later."""
    station, playing, pending, state, commands = await _engine_station(db_session, monkeypatch)
    res = await client.post(
        f"/api/v1/stations/{station.id}/queue/play-next", headers=auth_headers,
        json={"asset_id": str(playing.asset_id)},
    )
    assert res.status_code == 201
    assert commands == ["/flush"] and state.cued_entry_id is None
    # The engine asks again and gets the operator's item
    from app.services import playout_engine
    state.given = set()
    got = await playout_engine.handle_next(db_session, station.id, given=[])
    assert got["entry_id"] == res.json()["id"]
    # Nothing changed since: Next is a plain skip, no second flush
    commands.clear()
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade=hard", headers=auth_headers)
    assert res.json()["message"] == "Skipped" and commands == ["/skip"]


@pytest.mark.asyncio
async def test_play_next_in_a_songs_last_seconds_keeps_the_held_song_until_next(
    client, auth_headers, db_session, monkeypatch
):
    """A flush seconds before a song ends leaves the engine with nothing ready, so Play
    next alone keeps the pre-loaded song then; a Next still plays the operator's item."""
    station, playing, pending, state, commands = await _engine_station(
        db_session, monkeypatch, elapsed_seconds=160.0  # 20 s of 180 left
    )
    res = await client.post(
        f"/api/v1/stations/{station.id}/queue/play-next", headers=auth_headers,
        json={"asset_id": str(playing.asset_id)},
    )
    assert res.status_code == 201 and commands == [] and state.cued_entry_id == str(pending.id)
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade=hard", headers=auth_headers)
    assert res.json()["message"] == "Skipped" and commands == ["/flush", "/skip"]


@pytest.mark.asyncio
async def test_deleting_the_item_the_engine_holds_takes_it_back(client, auth_headers, db_session, monkeypatch):
    station, playing, pending, state, commands = await _engine_station(db_session, monkeypatch)
    res = await client.delete(f"/api/v1/stations/{station.id}/queue/{pending.id}", headers=auth_headers)
    assert res.status_code == 204
    assert commands == ["/flush"] and state.cued_entry_id is None


@pytest.mark.asyncio
async def test_an_ordinary_next_is_not_flushed_or_delayed(client, auth_headers, db_session, monkeypatch):
    import time as _time

    station, playing, pending, state, commands = await _engine_station(db_session, monkeypatch)
    t0 = _time.monotonic()
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert res.json()["message"] == "Skipped" and commands == ["/skip"]
    assert _time.monotonic() - t0 < 1.0
    assert state.cued_entry_id == str(pending.id)
