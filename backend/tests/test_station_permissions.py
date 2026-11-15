"""Staff are limited to the stations they are assigned to, and a protected
station (the FM feed) can only be operated by admins and people assigned to it."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.station import Station
from app.models.user import User, UserRole


async def _user(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    user = User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids)
    db.add(user)
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return user, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _stations(db):
    fm = Station(id=uuid.uuid4(), name="FM 107.9", timezone="UTC", protected=True)
    web = Station(id=uuid.uuid4(), name="Web only", timezone="UTC")
    other = Station(id=uuid.uuid4(), name="Other", timezone="UTC")
    db.add_all([fm, web, other])
    await db.commit()
    return fm, web, other


def _skip(station):
    return f"/api/v1/stations/{station.id}/queue/skip"


@pytest.mark.asyncio
async def test_unassigned_staff_operate_everything_except_the_protected_station(
    client: AsyncClient, db_session: AsyncSession
):
    fm, web, _ = await _stations(db_session)
    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post(_skip(web), headers=dj)).status_code != 403
    assert (await client.post(_skip(fm), headers=dj)).status_code == 403
    # looking is still allowed
    assert (await client.get(f"/api/v1/stations/{fm.id}/queue", headers=dj)).status_code == 200
    # admins are never limited
    _, admin = await _user(client, db_session, UserRole.ADMIN)
    assert (await client.post(_skip(fm), headers=admin)).status_code != 403


@pytest.mark.asyncio
async def test_assigned_staff_operate_only_their_stations(client: AsyncClient, db_session: AsyncSession):
    fm, web, other = await _stations(db_session)
    _, dj = await _user(client, db_session, UserRole.DJ, [str(fm.id), str(web.id)])
    assert (await client.post(_skip(fm), headers=dj)).status_code != 403
    assert (await client.post(_skip(web), headers=dj)).status_code != 403
    assert (await client.post(_skip(other), headers=dj)).status_code == 403
    assert (await client.post(f"/api/v1/stations/{other.id}/controls/stop", headers=dj)).status_code == 403
    assert (await client.post(_skip(other))).status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_assigns_stations_and_protects(client: AsyncClient, db_session: AsyncSession):
    fm, web, _ = await _stations(db_session)
    _, admin = await _user(client, db_session, UserRole.ADMIN)
    manager_user, manager = await _user(client, db_session, UserRole.MANAGER)

    # a manager cannot edit, unprotect or delete the protected station
    r = await client.put(f"/api/v1/stations/{fm.id}", json={"description": "x"}, headers=manager)
    assert r.status_code == 403
    assert (await client.delete(f"/api/v1/stations/{fm.id}", headers=manager)).status_code == 403
    # nor protect another one
    r = await client.put(f"/api/v1/stations/{web.id}", json={"protected": True}, headers=manager)
    assert r.status_code == 403
    r = await client.put(f"/api/v1/stations/{web.id}", json={"description": "fine"}, headers=manager)
    assert r.status_code == 200 and r.json()["protected"] is False

    # the admin assigns the manager to the FM station
    r = await client.put(
        f"/api/v1/users/{manager_user.id}", json={"station_ids": [str(fm.id)]}, headers=admin
    )
    assert r.status_code == 200 and r.json()["station_ids"] == [str(fm.id)]
    r = await client.put(f"/api/v1/stations/{fm.id}", json={"description": "x"}, headers=manager)
    assert r.status_code == 200
    assert (await client.post(_skip(web), headers=manager)).status_code == 403
    me = await client.get("/api/v1/auth/me", headers=manager)
    assert me.json()["station_ids"] == [str(fm.id)]

    # other edits leave the assignment alone; null clears it
    r = await client.put(f"/api/v1/users/{manager_user.id}", json={"title": "PD"}, headers=admin)
    assert r.json()["station_ids"] == [str(fm.id)]
    r = await client.put(f"/api/v1/users/{manager_user.id}", json={"station_ids": None}, headers=admin)
    assert r.json()["station_ids"] is None


@pytest.mark.asyncio
async def test_schedules_and_playlists_follow_station_permissions(
    client: AsyncClient, db_session: AsyncSession
):
    from app.models.schedule import Schedule

    fm, web, _ = await _stations(db_session)
    fm_schedule = Schedule(id=uuid.uuid4(), station_id=fm.id, name="FM week")
    web_schedule = Schedule(id=uuid.uuid4(), station_id=web.id, name="Web week")
    db_session.add_all([fm_schedule, web_schedule])
    await db_session.commit()
    _, manager = await _user(client, db_session, UserRole.MANAGER)
    _, admin = await _user(client, db_session, UserRole.ADMIN)

    # station named in the body
    r = await client.post("/api/v1/schedules/", json={"station_id": str(fm.id), "name": "x"}, headers=manager)
    assert r.status_code == 403
    r = await client.post("/api/v1/schedules/", json={"station_id": str(web.id), "name": "x"}, headers=manager)
    assert r.status_code == 201, r.text
    # station reached through the object in the path
    r = await client.patch(f"/api/v1/schedules/{fm_schedule.id}", json={"name": "y"}, headers=manager)
    assert r.status_code == 403
    r = await client.patch(f"/api/v1/schedules/{web_schedule.id}", json={"name": "y"}, headers=manager)
    assert r.status_code == 200, r.text
    assert (await client.delete(f"/api/v1/schedules/{fm_schedule.id}", headers=manager)).status_code == 403
    # ... or through a parent named in the body
    block = {"schedule_id": str(fm_schedule.id), "name": "b", "start_time": "06:00:00", "end_time": "09:00:00"}
    assert (await client.post("/api/v1/schedules/blocks", json=block, headers=manager)).status_code == 403
    # station in the query
    r = await client.post(
        "/api/v1/playlists/generation/regenerate", params={"station_id": str(fm.id)}, headers=manager
    )
    assert r.status_code == 403
    r = await client.put(
        "/api/v1/playlists/generation",
        json={"station_id": str(fm.id), "enabled": False, "hours": 6, "artist_distance": 2},
        headers=manager,
    )
    assert r.status_code == 403
    # reading stays open, and the admin is not limited
    assert (await client.get(f"/api/v1/schedules/{fm_schedule.id}", headers=manager)).status_code == 200
    r = await client.patch(f"/api/v1/schedules/{fm_schedule.id}", json={"name": "z"}, headers=admin)
    assert r.status_code == 200
