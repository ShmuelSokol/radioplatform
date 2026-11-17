"""A spot schedule with no station airs on every station, the protected FM station
included, so only admins and staff allowed on every station may make or change one
(review finding F-DATABANK-58)."""
import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.spot_schedule import SpotSchedule
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User, UserRole


async def _login(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _setup(db):
    fm = Station(id=uuid.uuid4(), name="FM 107.9", timezone="UTC", protected=True)
    web = Station(id=uuid.uuid4(), name="Web", timezone="UTC")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    spot = Asset(id=uuid.uuid4(), title="Bagel King spot", duration=30.0, file_path="b.mp3",
                 asset_type="spot", category="sponsor")
    db.add_all([fm, web, sponsor, spot])
    await db.flush()
    everywhere = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=None,
                              start_date=date.today(), end_date=date.today() + timedelta(days=30))
    db.add(everywhere)
    await db.commit()
    return fm, web, sponsor, spot, everywhere


@pytest.mark.asyncio
async def test_only_every_station_staff_touch_every_station_spot_schedules(
    client: AsyncClient, db_session: AsyncSession
):
    fm, web, sponsor, spot, everywhere = await _setup(db_session)
    web_only = await _login(client, db_session, UserRole.MANAGER, [str(web.id)])
    unassigned = await _login(client, db_session, UserRole.MANAGER)  # every station but the protected one
    both = await _login(client, db_session, UserRole.MANAGER, [str(fm.id), str(web.id)])
    admin = await _login(client, db_session, UserRole.ADMIN)
    new = {"sponsor_id": str(sponsor.id), "asset_id": str(spot.id)}
    later = (date.today() + timedelta(days=60)).isoformat()

    for refused in (web_only, unassigned):
        # make one for every station
        r = await client.post("/api/v1/spot-schedules", json=new, headers=refused)
        assert r.status_code == 403, r.text
        # change, widen to every station, or delete one
        r = await client.put(f"/api/v1/spot-schedules/{everywhere.id}", json={"end_date": later}, headers=refused)
        assert r.status_code == 403, r.text
        r = await client.delete(f"/api/v1/spot-schedules/{everywhere.id}", headers=refused)
        assert r.status_code == 403, r.text
        # move its end date from the Databank
        r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": later}, headers=refused)
        assert r.status_code == 403, r.text
        r = await client.patch(
            f"/api/v1/databank/spots/{spot.id}/end-date?station_id={web.id}", json={"end_date": later}, headers=refused
        )
        assert r.status_code == 403, r.text

    # Their own station's schedules still work
    r = await client.post("/api/v1/spot-schedules", json={**new, "station_id": str(web.id)}, headers=web_only)
    assert r.status_code == 201, r.text
    own = r.json()["id"]
    r = await client.put(f"/api/v1/spot-schedules/{own}", json={"station_id": None}, headers=web_only)
    assert r.status_code == 403, r.text

    # Staff on every station, and admins, may
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": later}, headers=both)
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/spot-schedules", json=new, headers=admin)
    assert r.status_code == 201, r.text
    r = await client.delete(f"/api/v1/spot-schedules/{everywhere.id}", headers=both)
    assert r.status_code == 204, r.text
