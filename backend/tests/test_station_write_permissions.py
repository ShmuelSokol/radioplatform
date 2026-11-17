"""Station permissions on the write routes that name the station in the body,
the query, or only through the object being changed: spot schedules, live
shows, program blocks, now-playing, song-request approval, weather readouts.

The rule (may_operate_station): admins operate every station; other staff the
stations in User.station_ids; with no list, every station that is not
protected. The protected station (the FM feed) needs an explicit assignment.
"""
import time
import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.asset import Asset
from app.models.live_show import LiveShow
from app.models.queue_entry import QueueEntry
from app.models.song_request import RequestStatus, SongRequest
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User, UserRole
from app.models.weather_readout import WeatherReadout

SAVE_LIMIT = 1.0  # checklist S-6: saves confirmed under 1 s


async def _login(db, role: UserRole, station_ids=None) -> dict:
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=True, station_ids=station_ids)
    db.add(u)
    await db.commit()
    return {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


async def _world(db):
    fm = Station(id=uuid.uuid4(), name="FM", timezone="UTC", protected=True)
    web = Station(id=uuid.uuid4(), name="Web", timezone="UTC")
    other = Station(id=uuid.uuid4(), name="Other", timezone="UTC")
    song = Asset(id=uuid.uuid4(), title="Song", file_path="s.mp3", duration=180, asset_type="music")
    spot = Asset(id=uuid.uuid4(), title="Spot", file_path="p.mp3", duration=30, asset_type="spot")
    sponsor = Sponsor(id=uuid.uuid4(), name="Sponsor", length_seconds=30, audio_file_path="p.mp3")
    db.add_all([fm, web, other, song, spot, sponsor])
    await db.commit()
    return fm, web, other, song, spot, sponsor


async def _timed(coro):
    t = time.perf_counter()
    r = await coro
    return r, time.perf_counter() - t


@pytest.mark.asyncio
async def test_now_playing_writes_need_the_station(client: AsyncClient, db_session: AsyncSession):
    fm, web, *_ = await _world(db_session)
    dj = await _login(db_session, UserRole.DJ)  # unassigned: every station but FM
    admin = await _login(db_session, UserRole.ADMIN)
    assert (await client.delete(f"/api/v1/now-playing/{fm.id}", headers=dj)).status_code == 403
    assert (await client.patch(f"/api/v1/now-playing/{fm.id}", json={}, headers=dj)).status_code == 403
    assert (await client.delete(f"/api/v1/now-playing/{web.id}", headers=dj)).status_code == 204
    assert (await client.delete(f"/api/v1/now-playing/{fm.id}", headers=admin)).status_code == 204
    # a garbage id is a validation error, not a crash
    assert (await client.delete("/api/v1/now-playing/not-an-id", headers=admin)).status_code == 422


@pytest.mark.asyncio
async def test_spot_schedules_follow_station_permissions(client: AsyncClient, db_session: AsyncSession):
    fm, web, other, _, spot, sponsor = await _world(db_session)
    limited = await _login(db_session, UserRole.MANAGER, [str(web.id)])
    body = {"sponsor_id": str(sponsor.id), "asset_id": str(spot.id), "fixed_times": "07:00"}

    r = await client.post("/api/v1/spot-schedules", json={**body, "station_id": str(other.id)}, headers=limited)
    assert r.status_code == 403
    r = await client.post("/api/v1/spot-schedules", json={**body, "station_id": str(fm.id)}, headers=limited)
    assert r.status_code == 403
    r, took = await _timed(client.post("/api/v1/spot-schedules", json={**body, "station_id": str(web.id)},
                                       headers=limited))
    assert r.status_code == 201, r.text
    assert took < SAVE_LIMIT
    own = r.json()["id"]

    # moving their own schedule onto a station they may not operate is refused
    r = await client.put(f"/api/v1/spot-schedules/{own}", json={"station_id": str(other.id)}, headers=limited)
    assert r.status_code == 403

    admin = await _login(db_session, UserRole.ADMIN)
    r = await client.post("/api/v1/spot-schedules", json={**body, "station_id": str(other.id)}, headers=admin)
    assert r.status_code == 201
    theirs = r.json()["id"]
    assert (await client.put(f"/api/v1/spot-schedules/{theirs}", json={"plays_per_daypart": 2},
                             headers=limited)).status_code == 403
    assert (await client.delete(f"/api/v1/spot-schedules/{theirs}", headers=limited)).status_code == 403
    r = await client.put("/api/v1/spot-schedules/timing-defaults",
                         json={"station_id": str(other.id), "on_the_hour": "hard", "other": "soft"}, headers=limited)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_live_shows_follow_station_permissions(client: AsyncClient, db_session: AsyncSession):
    fm, web, *_ = await _world(db_session)
    dj = await _login(db_session, UserRole.DJ)
    r = await client.post("/api/v1/live-shows", json={"station_id": str(fm.id), "title": "On FM"}, headers=dj)
    assert r.status_code == 403
    r, took = await _timed(client.post("/api/v1/live-shows", json={"station_id": str(web.id), "title": "Web"},
                                       headers=dj))
    assert r.status_code == 201, r.text
    assert took < SAVE_LIMIT

    show = LiveShow(id=uuid.uuid4(), station_id=fm.id, title="FM show")
    db_session.add(show)
    await db_session.commit()
    for method, path, body in [
        ("PATCH", f"/api/v1/live-shows/{show.id}", {"title": "x"}),
        ("POST", f"/api/v1/live-shows/{show.id}/start", None),
        ("POST", f"/api/v1/live-shows/{show.id}/end", None),
    ]:
        r = await client.request(method, path, json=body, headers=dj)
        assert r.status_code == 403, (method, path, r.status_code)
    # the show was not touched
    await db_session.refresh(show)
    assert show.title == "FM show"


@pytest.mark.asyncio
async def test_program_blocks_follow_station_permissions(client: AsyncClient, db_session: AsyncSession):
    fm, web, other, song, *_ = await _world(db_session)
    limited = await _login(db_session, UserRole.MANAGER, [str(web.id)])
    admin = await _login(db_session, UserRole.ADMIN)

    def body(station):
        return {"station_id": str(station.id), "name": "Block", "times": "10:00",
                "items": [{"kind": "asset", "asset_id": str(song.id)}]}

    assert (await client.post("/api/v1/program-blocks", json=body(fm), headers=limited)).status_code == 403
    assert (await client.post("/api/v1/program-blocks", json=body(other), headers=limited)).status_code == 403
    r, took = await _timed(client.post("/api/v1/program-blocks", json=body(web), headers=limited))
    assert r.status_code == 201, r.text
    assert took < SAVE_LIMIT

    r = await client.post("/api/v1/program-blocks", json=body(other), headers=admin)
    assert r.status_code == 201, r.text
    block = r.json()["id"]
    assert (await client.put(f"/api/v1/program-blocks/{block}", json={"name": "x"},
                             headers=limited)).status_code == 403
    assert (await client.delete(f"/api/v1/program-blocks/{block}", headers=limited)).status_code == 403


@pytest.mark.asyncio
async def test_approving_a_song_request_needs_the_station(client: AsyncClient, db_session: AsyncSession):
    fm, web, _, song, *_ = await _world(db_session)
    manager = await _login(db_session, UserRole.MANAGER)  # unassigned: not FM
    on_fm = SongRequest(id=uuid.uuid4(), station_id=fm.id, requester_name="A", song_title="Song",
                        asset_id=song.id, status=RequestStatus.PENDING)
    on_web = SongRequest(id=uuid.uuid4(), station_id=web.id, requester_name="B", song_title="Song",
                         asset_id=song.id, status=RequestStatus.PENDING)
    db_session.add_all([on_fm, on_web])
    await db_session.commit()

    r = await client.patch(f"/api/v1/song-requests/{on_fm.id}", json={"status": "approved"}, headers=manager)
    assert r.status_code == 403
    fm_queue = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == fm.id))).scalars().all()
    assert fm_queue == []

    r = await client.patch(f"/api/v1/song-requests/{on_web.id}", json={"status": "approved"}, headers=manager)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_queuing_a_weather_readout_needs_the_station(client: AsyncClient, db_session: AsyncSession):
    fm, web, _, song, *_ = await _world(db_session)
    manager = await _login(db_session, UserRole.MANAGER)
    readouts = {}
    for st in (fm, web):
        ro = WeatherReadout(id=uuid.uuid4(), station_id=st.id, readout_date=date.today(),
                            script_text="Sunny", asset_id=song.id, status="ready")
        db_session.add(ro)
        readouts[st.name] = ro
    await db_session.commit()

    r = await client.post(f"/api/v1/weather-readouts/{readouts['FM'].id}/queue", headers=manager)
    assert r.status_code == 403
    r = await client.post(f"/api/v1/weather-readouts/{readouts['Web'].id}/queue", headers=manager)
    assert r.status_code == 200, r.text
    q = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == web.id))).scalars().all()
    assert [e.asset_id for e in q] == [song.id]
