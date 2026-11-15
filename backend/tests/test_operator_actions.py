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
