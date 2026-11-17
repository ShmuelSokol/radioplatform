"""Databank rows left after #171 (docs/test-checklist.md): Find with % and _
(F-DATABANK-57), the end-date edit with no station (F-DATABANK-60) and an item edit
on air / two managers (F-DATABANK-36, F-DATABANK-52). The screen side is in
frontend/e2e/databank-screen.mjs."""
import os
import time
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.core.security import hash_password
from app.models.asset import Asset
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User, UserRole

SAVE_LIMIT_S = 2.0 if os.environ.get("CI") else 1.0


def _asset(title, **kw):
    return Asset(id=uuid.uuid4(), title=title, duration=kw.pop("duration", 200.0),
                 file_path=kw.pop("file_path", f"https://cdn.example.com/audio/{title}.mp3"),
                 asset_type=kw.pop("asset_type", "music"), **kw)


async def _login(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _items(client, headers, **params):
    r = await client.get("/api/v1/databank/items", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _titles(body):
    return [i["title"] for i in body["items"]]


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)



async def _spot_world(db):
    ny = Station(id=uuid.uuid4(), name="NY", timezone="America/New_York")
    jm = Station(id=uuid.uuid4(), name="JM", timezone="Asia/Jerusalem")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    db.add_all([ny, jm, sponsor])
    await db.flush()
    return ny, jm, sponsor


def _sched(sponsor, spot, station, start=None, end=None, active=True):
    return SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id,
                        station_id=station.id if station else None,
                        start_date=start, end_date=end, is_active=active)


@pytest.mark.asyncio
async def test_find_treats_percent_and_underscore_as_letters(client, auth_headers, db_session):
    db_session.add_all([
        _asset("100% Simcha"),
        _asset("100 Simcha"),
        _asset("my_song", file_path="https://x/a/my_song.mp3"),
        _asset("mysong", file_path="https://x/a/mysong.mp3"),
        _asset("Back\\slash"),
    ])
    await db_session.commit()
    assert _titles(await _items(client, auth_headers, search="100%")) == ["100% Simcha"]
    assert _titles(await _items(client, auth_headers, search="my_")) == ["my_song"]
    assert _titles(await _items(client, auth_headers, search="k\\s")) == ["Back\\slash"]
    assert _titles(await _items(client, auth_headers, search="%")) == ["100% Simcha"]


@pytest.mark.asyncio
async def test_end_date_without_a_station_is_refused_and_changes_nothing(client, db_session):
    """F-DATABANK-60: with no station_id the API refuses (403 for a manager not on every
    station, 400 "Choose a station first" otherwise) and nothing changes; with the station it
    moves that station's schedule only. The page itself always sends the chosen station
    (see e2e/databank-screen.mjs)."""
    ny, jm, sponsor = await _spot_world(db_session)
    spot = _asset("Spot", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    s1, s2 = _sched(sponsor, spot, ny, end=date.today()), _sched(sponsor, spot, jm, end=date.today())
    db_session.add_all([s1, s2])
    await db_session.commit()
    new_end = (date.today() + timedelta(days=3)).isoformat()
    ny_only = await _login(client, db_session, UserRole.MANAGER, [str(ny.id)])
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": new_end}, headers=ny_only)
    assert r.status_code == 403
    ends = [s.end_date for s in (await db_session.execute(
        select(SpotSchedule).execution_options(populate_existing=True))).scalars()]
    assert ends == [date.today(), date.today()]
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={ny.id}",
                           json={"end_date": new_end}, headers=ny_only)
    assert r.status_code == 200 and r.json()["updated"] == 1
    both = await _login(client, db_session, UserRole.MANAGER, [str(ny.id), str(jm.id)])
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": new_end}, headers=both)
    assert r.status_code == 400 and r.json()["detail"].startswith("Choose a station first")
    ends = sorted(str(s.end_date) for s in (await db_session.execute(
        select(SpotSchedule).execution_options(populate_existing=True))).scalars())
    assert ends == sorted([str(date.today()), new_end])  # only the NY one moved, by the call with the station


@pytest.mark.asyncio
async def test_item_edit_shows_in_databank_and_up_next_and_last_save_wins(client, auth_headers, db_session):
    """F-DATABANK-36, F-DATABANK-52 (server side): nothing caches the old title"""
    from tests.test_queue_advance import _station_with_two_tracks

    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    other = await _login(client, db_session, UserRole.MANAGER)
    t0 = time.perf_counter()
    r = await client.patch(f"/api/v1/assets/{pending.asset_id}", json={"title": "First edit", "category": "slow"},
                           headers=auth_headers)
    assert r.status_code == 200 and time.perf_counter() - t0 < SAVE_LIMIT_S  # F-DATABANK-26
    r = await client.patch(f"/api/v1/assets/{pending.asset_id}", json={"title": "Second edit", "vote": 77},
                           headers=other)
    assert r.status_code == 200
    row = next(i for i in (await _items(client, auth_headers, sort="title"))["items"]
               if i["id"] == str(pending.asset_id))
    assert (row["title"], row["type"], row["vote"]) == ("Second edit", "slow", 77)
    q = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    titles = {e["asset_id"]: e["asset"]["title"] for e in q["entries"]}
    assert titles[str(pending.asset_id)] == "Second edit"
    r = await client.patch(f"/api/v1/assets/{playing.asset_id}", json={"title": "On air renamed"},
                           headers=auth_headers)
    np_ = (await client.get(f"/api/v1/now-playing/{station.id}")).json()
    assert "On air renamed" in str(np_)
