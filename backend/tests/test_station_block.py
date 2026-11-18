"""Control room quick action "Not on this station" (POST /assets/{id}/station-block).

The library is shared by every station, so "Do not play" takes a song off all of them.
Shmuel, 2026-11-18, was not sure he wants that; this second choice keeps a song off
only the station whose control room you are in. The promise tested here: it never
changes anything on another station, and it clears that station's waiting copies the
same way the all-stations action does.
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.v1.queue import _pick_next_pending, _skip_placeholders
from app.core.security import hash_password
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.station_blocked_asset import StationBlockedAsset
from app.models.user import User, UserRole
from app.services import playout_engine
from app.services.playout_plan import build_plan
from app.services.scheduling import SchedulingService
from app.services.station_blocks import blocked_asset_ids, is_blocked
from tests.test_song_quick_actions import _asset, _entry, _left, _setup


def _block(client, headers, song_id, station_id, on=True):
    return client.post(f"/api/v1/assets/{song_id}/station-block", headers=headers,
                       json={"station_id": str(station_id), "on": on})


@pytest.mark.asyncio
async def test_block_clears_only_this_stations_waiting_copies(client, auth_headers, db_session):
    a, b, song, other, rows = await _setup(db_session)
    ids = {k: v.id for k, v in rows.items()}
    a_id, b_id, song_id = a.id, b.id, song.id
    r = await _block(client, auth_headers, song_id, a_id)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": str(song_id), "station_id": str(a_id), "station_name": "Quick FM",
                        "blocked": True, "removed_from_queue": 2, "still_to_play": 2}
    # station A: untimed waiting copies gone; on air, timed, played and the other song stay.
    # station B: both copies still wait.
    assert await _left(db_session, ids) == {"on_air", "other", "timed", "played", "b_first", "b_second"}
    assert (await db_session.get(QueueEntry, ids["on_air"])).status == "playing"
    # the song itself is unchanged: same category, still playable everywhere else
    assert (await db_session.get(Asset, song_id)).category == "Lively"
    assert await is_blocked(db_session, a_id, song_id) is True
    assert await is_blocked(db_session, b_id, song_id) is False
    state = await client.get(f"/api/v1/assets/{song_id}/station-block?station_id={b_id}", headers=auth_headers)
    assert state.json()["blocked"] is False
    state = await client.get(f"/api/v1/assets/{song_id}/station-block?station_id={a_id}", headers=auth_headers)
    assert state.json()["blocked"] is True


@pytest.mark.asyncio
async def test_removes_the_same_copies_as_do_not_play_would_on_that_station(client, auth_headers, db_session):
    """Same setup twice: what the per-station action removes on station A is exactly
    what the all-stations action removes on station A."""
    a, b, song, other, rows = await _setup(db_session)
    ids = {k: v.id for k, v in rows.items()}
    a_names = {"on_air", "other", "auto", "manual", "timed", "played"}
    await _block(client, auth_headers, song.id, a.id)
    per_station = await _left(db_session, ids) & a_names

    # the same queue again on two fresh stations (station names are unique)
    for station in (await db_session.execute(select(Station))).scalars().all():
        station.name = f"old {station.id}"
    await db_session.commit()
    a2, b2, song2, other2, rows2 = await _setup(db_session)
    ids2 = {k: v.id for k, v in rows2.items()}
    await client.post(f"/api/v1/assets/{song2.id}/do-not-play", headers=auth_headers)
    everywhere = await _left(db_session, ids2) & a_names
    assert per_station == everywhere == {"on_air", "other", "timed", "played"}


@pytest.mark.asyncio
async def test_copy_held_by_the_engine_is_left(client, auth_headers, db_session, monkeypatch):
    a, b, song, other, rows = await _setup(db_session)
    ids = {k: v.id for k, v in rows.items()}
    a_id = a.id
    monkeypatch.setattr(playout_engine, "held_entry_ids",
                        lambda station_id: {ids["auto"]} if station_id == a_id else set())
    r = await _block(client, auth_headers, song.id, a_id)
    assert r.json()["removed_from_queue"] == 1 and r.json()["still_to_play"] == 3
    assert "auto" in await _left(db_session, ids)


@pytest.mark.asyncio
async def test_undo_and_pressing_twice(client, auth_headers, db_session):
    a, b, song, other, rows = await _setup(db_session)
    a_id, b_id, song_id = a.id, b.id, song.id
    await _block(client, auth_headers, song_id, a_id)
    again = await _block(client, auth_headers, song_id, a_id)
    assert again.status_code == 200 and again.json()["removed_from_queue"] == 0
    # blocking on B as well is its own switch
    await _block(client, auth_headers, song_id, b_id)
    r = await _block(client, auth_headers, song_id, a_id, on=False)
    assert r.status_code == 200 and r.json()["blocked"] is False
    assert await is_blocked(db_session, a_id, song_id) is False
    assert await is_blocked(db_session, b_id, song_id) is True
    # undo on a station where it was never blocked changes nothing
    r = await _block(client, auth_headers, song_id, a_id, on=False)
    assert r.status_code == 200
    rows = (await db_session.execute(select(StationBlockedAsset))).scalars().all()
    assert [(x.station_id, x.asset_id) for x in rows] == [(b_id, song_id)]


@pytest.mark.asyncio
async def test_block_is_separate_from_do_not_play(client, auth_headers, db_session):
    """Each has its own undo: allowing the song again everywhere leaves the station
    block, and lifting the station block leaves do-not-play."""
    a, b, song, other, rows = await _setup(db_session)
    a_id, song_id = a.id, song.id
    await _block(client, auth_headers, song_id, a_id)
    await client.post(f"/api/v1/assets/{song_id}/do-not-play", headers=auth_headers)
    await client.post(f"/api/v1/assets/{song_id}/do-not-play", headers=auth_headers, json={"on": False})
    assert await is_blocked(db_session, a_id, song_id) is True
    db_session.expire_all()
    assert (await db_session.get(Asset, song_id)).category == "Lively"
    await client.post(f"/api/v1/assets/{song_id}/do-not-play", headers=auth_headers)
    await _block(client, auth_headers, song_id, a_id, on=False)
    db_session.expire_all()
    assert (await db_session.get(Asset, song_id)).category == "do_not_play"


@pytest.mark.asyncio
async def test_blocked_song_is_never_picked_on_that_station_only(db_session):
    """A copy that reaches the queue anyway (a playlist naming the song, an old row) is
    passed over on the blocked station by both players; the other station plays it."""
    a = Station(id=uuid.uuid4(), name="Blocked FM", timezone="America/New_York")
    b = Station(id=uuid.uuid4(), name="Open FM", timezone="America/New_York")
    song, good = _asset("Unwanted here"), _asset("Good song")
    db_session.add_all([a, b, song, good])
    await db_session.flush()
    a_auto, a_good = _entry(a, song, 1), _entry(a, good, 2)
    b_auto = _entry(b, song, 1)
    db_session.add_all([a_auto, a_good, b_auto, StationBlockedAsset(station_id=a.id, asset_id=song.id)])
    await db_session.commit()
    a_id, b_id, song_id, good_id = a.id, b.id, song.id, good.id
    a_auto_id, b_auto_id = a_auto.id, b_auto.id

    # the playout engine (read-only pick) and the local FM box's plan
    assert (await playout_engine.pick_next(db_session, a_id)).asset_id == good_id
    assert (await playout_engine.pick_next(db_session, b_id)).asset_id == song_id
    assert [i["title"] for i in (await build_plan(db_session, a_id))["entries"]] == ["Good song"]
    assert [i["title"] for i in (await build_plan(db_session, b_id))["entries"]] == ["Unwanted here"]

    # the browser player: the copy is marked skipped on A, untouched on B
    now = datetime.now(UTC)
    assert (await _pick_next_pending(db_session, a_id, now)).asset_id == good_id
    assert (await _pick_next_pending(db_session, b_id, now)).asset_id == song_id
    await _skip_placeholders(db_session, b_id)
    db_session.expire_all()
    assert (await db_session.get(QueueEntry, a_auto_id)).status == "skipped"
    assert (await db_session.get(QueueEntry, b_auto_id)).status == "pending"

    # what an operator queues by hand still plays, as with do-not-play
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=a_id, asset_id=song_id, position=0,
                              status="pending", source="manual"))
    await db_session.commit()
    assert (await playout_engine.pick_next(db_session, a_id)).asset_id == song_id
    assert (await _pick_next_pending(db_session, a_id, now)).asset_id == song_id


@pytest.mark.asyncio
async def test_automatic_draws_leave_the_song_out_on_that_station_only(db_session):
    from app.services.playlist_generator_service import _Library
    from app.services.queue_replenish_service import QueueReplenishService

    a = Station(id=uuid.uuid4(), name="Blocked FM", timezone="America/New_York")
    b = Station(id=uuid.uuid4(), name="Open FM", timezone="America/New_York")
    song = _asset("Only song")
    db_session.add_all([a, b, song])
    await db_session.flush()
    db_session.add(StationBlockedAsset(station_id=a.id, asset_id=song.id))
    await db_session.commit()
    a_id, b_id, song_id = a.id, b.id, song.id

    # playlist generation
    assert await _Library(db_session, a_id, set()).pool("music", None) == []
    assert [r.id for r in await _Library(db_session, b_id, set()).pool("music", None)] == [song_id]
    assert await _Library(db_session, a_id, set()).pool("music", "Lively") == []
    # rule and fill draws of the queue refill
    for station_id, expected in ((a_id, []), (b_id, [song_id])):
        service = QueueReplenishService(db_session, station_id)
        found = (await db_session.execute(service._apply_oldies_filter(select(Asset.id)))).scalars().all()
        assert found == expected
    # template slots
    picker = SchedulingService(db_session)
    assert await picker._pick_asset_for_slot("music", None, a_id) is None
    assert await picker._pick_asset_for_slot("music", None, b_id) == song_id
    assert await picker._pick_asset_for_slot("music", "Lively", a_id) is None
    # the subquery every pick shares
    assert (await db_session.execute(blocked_asset_ids(str(a_id)))).scalars().all() == [song_id]
    assert (await db_session.execute(blocked_asset_ids(b_id))).scalars().all() == []


@pytest.mark.asyncio
async def test_refill_purge_removes_blocked_automatic_copies_here_only(db_session):
    from app.services.queue_replenish_service import QueueReplenishService

    a, b, song, other, rows = await _setup(db_session)
    ids = {k: v.id for k, v in rows.items()}
    a_id = a.id
    db_session.add(StationBlockedAsset(station_id=a_id, asset_id=song.id))
    await db_session.commit()
    await QueueReplenishService(db_session, a_id)._purge_placeholders(keep_ad_slots=False)
    await db_session.commit()
    # automatic copies go (timed block ones too, as for a placeholder); the operator's stays
    assert await _left(db_session, ids) == {"on_air", "other", "manual", "played", "b_first", "b_second"}


@pytest.mark.asyncio
async def test_bad_input_unknown_song_and_station(client, auth_headers, db_session):
    a, b, song, other, rows = await _setup(db_session)
    a_id, song_id = a.id, song.id
    url = f"/api/v1/assets/{song_id}/station-block"
    assert (await client.post(url, headers=auth_headers, json={"on": True})).status_code == 422
    assert (await client.post(url, headers=auth_headers, json={"station_id": "nope"})).status_code == 422
    assert (await client.post(url, headers=auth_headers,
                              json={"station_id": str(a_id), "on": "yes"})).status_code == 422
    assert (await _block(client, auth_headers, song_id, uuid.uuid4())).status_code == 404
    assert (await _block(client, auth_headers, uuid.uuid4(), a_id)).status_code == 404
    assert (await client.post(url, json={"station_id": str(a_id)})).status_code in (401, 403)
    assert (await db_session.execute(select(StationBlockedAsset))).scalars().all() == []


async def _login(client, db, role, station_ids=None):
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.com",
                hashed_password=hash_password("pw-123456"), role=role, is_active=True,
                station_ids=station_ids)
    email = user.email
    db.add(user)
    await db.commit()
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw-123456"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.DJ, UserRole.VIEWER])
async def test_only_managers_may_block(client, db_session, role):
    a, b, song, other, rows = await _setup(db_session)
    a_id, song_id = a.id, song.id
    headers = await _login(client, db_session, role)
    assert (await _block(client, headers, song_id, a_id)).status_code == 403
    assert await is_blocked(db_session, a_id, song_id) is False
    # reading the state is open to every staff role
    state = await client.get(f"/api/v1/assets/{song_id}/station-block?station_id={a_id}", headers=headers)
    assert state.status_code == 200


@pytest.mark.asyncio
async def test_manager_limited_to_one_station_cannot_block_on_another(client, db_session):
    a, b, song, other, rows = await _setup(db_session)
    a_id, b_id, song_id = a.id, b.id, song.id
    headers = await _login(client, db_session, UserRole.MANAGER, station_ids=[str(a_id)])
    assert (await _block(client, headers, song_id, b_id)).status_code == 403
    assert await is_blocked(db_session, b_id, song_id) is False
    assert (await _block(client, headers, song_id, a_id)).status_code == 200
