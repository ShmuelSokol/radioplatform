"""Listener club (CRM): PIN sign-in, ratings, raffles, and the admin side.
B-CRM-1..16, B-ROLE-20, V1-107, V1-151."""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.asset import Asset
from app.models.user import User, UserRole
from tests.timing import limit

FAST = 0.3


async def _staff(db, role=UserRole.MANAGER) -> dict:
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=True)
    db.add(u)
    await db.commit()
    return {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


async def _member(client, name="Listener") -> tuple[dict, dict]:
    r = await client.post("/api/v1/crm/register", json={"name": name, "phone": "+15550009999"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["pin"]) == 6 and body["pin"].isdigit()
    return body, {"X-CRM-PIN": body["pin"]}


@pytest.mark.asyncio
async def test_register_login_rate_and_unrate(client: AsyncClient, db_session: AsyncSession):
    song = Asset(id=uuid.uuid4(), title="Niggun", artist="Choir", file_path="n.mp3", duration=100)
    db_session.add(song)
    await db_session.commit()
    member, pin = await _member(client)
    assert (await client.post("/api/v1/crm/register", json={"name": "  "})).status_code == 400

    started = time.perf_counter()
    r = await client.post("/api/v1/crm/login", json={"pin": member["pin"]})
    assert r.status_code == 200 and r.json()["name"] == "Listener"
    assert time.perf_counter() - started < limit(FAST)
    assert (await client.post("/api/v1/crm/login", json={"pin": "000000" if member["pin"] != "000000" else "111111"})
            ).status_code == 401

    r = await client.post("/api/v1/crm/rate", json={"asset_id": str(song.id), "rating": 4}, headers=pin)
    assert r.status_code == 200, r.text
    # upsert: rating again changes it, does not add a second one
    r = await client.post("/api/v1/crm/rate", json={"asset_id": str(song.id), "rating": 5, "is_favorite": True},
                          headers=pin)
    assert r.json()["rating"] == 5
    mine = (await client.get("/api/v1/crm/my-ratings", headers=pin)).json()
    assert [(m["asset_title"], m["rating"], m["is_favorite"]) for m in mine] == [("Niggun", 5, True)]
    assert (await client.post("/api/v1/crm/rate", json={"asset_id": str(song.id), "rating": 9},
                              headers=pin)).status_code == 422
    assert (await client.post("/api/v1/crm/rate", json={"asset_id": str(uuid.uuid4()), "rating": 3},
                              headers=pin)).status_code == 404

    manager = await _staff(db_session)
    ranks = (await client.get("/api/v1/crm/song-rankings", headers=manager)).json()
    assert ranks[0]["title"] == "Niggun" and ranks[0]["favorite_count"] == 1

    assert (await client.delete(f"/api/v1/crm/rate/{song.id}", headers=pin)).status_code == 204
    assert (await client.get("/api/v1/crm/my-ratings", headers=pin)).json() == []
    assert (await client.delete(f"/api/v1/crm/rate/{song.id}", headers=pin)).status_code == 404


@pytest.mark.asyncio
async def test_member_and_staff_logins_do_not_cross(client: AsyncClient, db_session: AsyncSession):
    """B-ROLE-20"""
    _, pin = await _member(client)
    manager = await _staff(db_session)
    # a staff token is not a member login
    assert (await client.get("/api/v1/crm/my-ratings", headers=manager)).status_code in (401, 422)
    assert (await client.get("/api/v1/crm/my-ratings", headers={"X-CRM-PIN": "12345x"})).status_code == 401
    # a member PIN is not a staff login
    assert (await client.get("/api/v1/crm/members", headers=pin)).status_code in (401, 403)
    assert (await client.post("/api/v1/crm/raffles", json={}, headers=pin)).status_code in (401, 403)
    dj = await _staff(db_session, UserRole.DJ)
    assert (await client.get("/api/v1/crm/members", headers=dj)).status_code == 403


@pytest.mark.asyncio
async def test_admin_member_list_and_soft_delete(client: AsyncClient, db_session: AsyncSession):
    member, pin = await _member(client, "Chaim")
    await _member(client, "Other")
    manager = await _staff(db_session)
    r = await client.get("/api/v1/crm/members", params={"search": "Chai"}, headers=manager)
    assert r.status_code == 200
    assert [m["name"] for m in r.json()["members"]] == ["Chaim"]
    assert (await client.get(f"/api/v1/crm/members/{member['id']}", headers=manager)).json()["name"] == "Chaim"
    assert (await client.get(f"/api/v1/crm/members/{uuid.uuid4()}", headers=manager)).status_code == 404
    assert (await client.delete(f"/api/v1/crm/members/{member['id']}", headers=manager)).status_code == 204
    # deactivated: the PIN no longer signs in
    assert (await client.post("/api/v1/crm/login", json={"pin": member["pin"]})).status_code == 401
    assert (await client.get("/api/v1/crm/my-ratings", headers=pin)).status_code == 401


@pytest.mark.asyncio
async def test_raffle_lifecycle(client: AsyncClient, db_session: AsyncSession):
    """B-CRM-6/7/12..16, V1-151"""
    manager = await _staff(db_session)
    now = datetime.now(timezone.utc)
    body = {"title": "Seforim set", "prize": "Shas", "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(days=1)).isoformat()}
    r = await client.post("/api/v1/crm/raffles", json=body, headers=manager)
    assert r.status_code == 201, r.text
    raffle = r.json()
    future = await client.post("/api/v1/crm/raffles", headers=manager, json={
        **body, "title": "Later", "starts_at": (now + timedelta(days=2)).isoformat(),
        "ends_at": (now + timedelta(days=3)).isoformat()})
    later = future.json()

    active = (await client.get("/api/v1/crm/raffles/active")).json()
    assert "Seforim set" in [a["title"] for a in active]

    # nobody entered yet: drawing is refused
    assert (await client.post(f"/api/v1/crm/raffles/{raffle['id']}/draw", headers=manager)).status_code == 400

    a, pin_a = await _member(client, "A")
    _, pin_b = await _member(client, "B")
    assert (await client.post(f"/api/v1/crm/raffles/{raffle['id']}/enter", headers=pin_a)).status_code == 201
    assert (await client.post(f"/api/v1/crm/raffles/{raffle['id']}/enter", headers=pin_a)).status_code == 409
    assert (await client.post(f"/api/v1/crm/raffles/{raffle['id']}/enter", headers=pin_b)).status_code == 201
    assert (await client.post(f"/api/v1/crm/raffles/{later['id']}/enter", headers=pin_a)).status_code == 400
    assert (await client.post(f"/api/v1/crm/raffles/{uuid.uuid4()}/enter", headers=pin_a)).status_code == 404

    entries = (await client.get(f"/api/v1/crm/raffles/{raffle['id']}/entries", headers=manager)).json()
    assert sorted(e["member_name"] for e in entries) == ["A", "B"]

    r = await client.post(f"/api/v1/crm/raffles/{raffle['id']}/draw", headers=manager)
    assert r.status_code == 200, r.text
    drawn = r.json()
    assert drawn["status"] == "drawn" and drawn["winner_name"] in ("A", "B") and drawn["entry_count"] == 2
    # drawn raffles: no second draw, no more entries
    assert (await client.post(f"/api/v1/crm/raffles/{raffle['id']}/draw", headers=manager)).status_code == 400
    _, pin_c = await _member(client, "C")
    assert (await client.post(f"/api/v1/crm/raffles/{raffle['id']}/enter", headers=pin_c)).status_code == 400

    r = await client.patch(f"/api/v1/crm/raffles/{later['id']}", json={"status": "closed", "title": "Closed"},
                           headers=manager)
    assert r.status_code == 200 and r.json()["title"] == "Closed"
    all_raffles = (await client.get("/api/v1/crm/raffles", headers=manager)).json()
    assert {x["title"] for x in all_raffles} == {"Seforim set", "Closed"}


@pytest.mark.asyncio
async def test_pin_guessing_is_slowed_down(client: AsyncClient, db_session: AsyncSession):
    codes = set()
    for i in range(40):
        r = await client.post("/api/v1/crm/login", json={"pin": f"{900000 + i:06d}"})
        codes.add(r.status_code)
    assert 429 in codes
