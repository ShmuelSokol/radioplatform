"""Role gauntlet: each role reaches what it should and nothing else."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole


async def _headers(client: AsyncClient, db: AsyncSession, role: UserRole) -> dict:
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# (path, roles allowed). Everyone else must get 403.
STAFF = {"admin", "manager", "dj", "viewer"}
CASES = [
    ("/api/v1/assets", STAFF),
    ("/api/v1/databank/items", STAFF),
    ("/api/v1/categories", STAFF),
    ("/api/v1/asset-types", STAFF),
    ("/api/v1/rules", STAFF),
    ("/api/v1/reviews/queues", STAFF),
    ("/api/v1/live-shows", STAFF),  # the dashboard shows the on-air show to every staff role
    ("/api/v1/playlists", STAFF),  # the blocks screen names each block's playlist for every staff role
    ("/api/v1/users", {"admin"}),
    ("/api/v1/sponsors", {"admin", "manager"}),
    ("/api/v1/campaigns", STAFF | {"sponsor"}),  # dj/viewer: admin Campaigns screen, view only
    ("/api/v1/alerts", {"admin", "manager"}),
    ("/api/v1/emergency", STAFF),  # the Emergency Alerts page: every staff role sees the log
    ("/api/v1/auth/me", STAFF | {"sponsor"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(UserRole))
async def test_role_matrix(client: AsyncClient, db_session: AsyncSession, role: UserRole):
    headers = await _headers(client, db_session, role)
    for path, allowed in CASES:
        r = await client.get(path, headers=headers)
        if role.value in allowed:
            assert r.status_code == 200, f"{role.value} should read {path}, got {r.status_code}"
        else:
            assert r.status_code == 403, f"{role.value} must not read {path}, got {r.status_code}"


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: AsyncClient):
    for path, _ in CASES:
        r = await client.get(path)
        assert r.status_code in (401, 403), f"anonymous reached {path}: {r.status_code}"


@pytest.mark.asyncio
async def test_every_role_sees_the_same_schedule_list_in_a_fixed_order(client: AsyncClient, db_session: AsyncSession):
    """The schedule list is not cut down by role (a live check wondered whether a
    viewer's one-schedule list was the whole list), and it has an order: highest
    priority first, then by name."""
    from app.models.schedule import Schedule
    from app.models.station import Station

    station = Station(name="Order FM", timezone="UTC")
    db_session.add(station)
    await db_session.flush()
    for name, priority in [("Night", 0), ("Main Schedule", 5), ("Afternoon", 0), ("Yom Tov", 9)]:
        db_session.add(Schedule(station_id=station.id, name=name, priority=priority, is_active=True))
    await db_session.commit()

    seen = {}
    for role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.DJ, UserRole.VIEWER):
        r = await client.get("/api/v1/schedules", headers=await _headers(client, db_session, role))
        assert r.status_code == 200
        seen[role] = [s["name"] for s in r.json()]
    assert seen[UserRole.ADMIN] == ["Yom Tov", "Main Schedule", "Afternoon", "Night"]
    assert all(names == seen[UserRole.ADMIN] for names in seen.values())
