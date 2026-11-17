"""F-CAMPAIGNS-1: the admin Campaigns screen (/admin/campaigns).

Managers create a campaign for a named sponsor and change its status; DJs and
viewers may look at every campaign, its drafts and comments, but change nothing;
the list filters by sponsor; a campaign read names its sponsor.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.sponsor import Sponsor
from app.models.user import User, UserRole


async def _headers(db, role):
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=True)
    db.add(u)
    await db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


@pytest.mark.asyncio
async def test_staff_read_campaigns_managers_change_them(client: AsyncClient, db_session: AsyncSession):
    """F-CAMPAIGNS-1: manager creates for a sponsor, filters by sponsor, changes
    status; dj and viewer read list/detail/drafts/comments and are refused writes."""
    shop_user, shop = await _headers(db_session, UserRole.SPONSOR)
    a = Sponsor(id=uuid.uuid4(), name="A Shop", length_seconds=30, audio_file_path="", user_id=shop_user.id)
    b = Sponsor(id=uuid.uuid4(), name="B Shop", length_seconds=30, audio_file_path="")
    db_session.add_all([a, b])
    await db_session.commit()
    _, manager = await _headers(db_session, UserRole.MANAGER)
    _, dj = await _headers(db_session, UserRole.DJ)
    _, viewer = await _headers(db_session, UserRole.VIEWER)

    r = await client.post("/api/v1/campaigns", headers=manager, json={
        "name": "Pesach sale", "sponsor_id": str(a.id), "description": "Two weeks before Pesach",
        "start_date": "2027-04-01", "end_date": "2027-04-14", "budget_cents": 50000})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["sponsor_id"] == str(a.id) and r.json()["status"] == "draft"
    r = await client.post("/api/v1/campaigns", headers=manager, json={"name": "Sukkos", "sponsor_id": str(b.id)})
    assert r.status_code == 201, r.text
    assert (await client.post(f"/api/v1/campaigns/{cid}/drafts", headers=manager,
                              json={"script_text": "Brought to you by A Shop"})).status_code == 201
    assert (await client.post(f"/api/v1/campaigns/{cid}/comments", headers=manager,
                              json={"body": "Please confirm the dates"})).status_code == 201

    # sponsor filter
    r = await client.get("/api/v1/campaigns", params={"sponsor_id": str(a.id)}, headers=manager)
    assert [c["name"] for c in r.json()] == ["Pesach sale"]
    assert r.json()[0]["sponsor_name"] == "A Shop"
    assert len((await client.get("/api/v1/campaigns", params={"limit": 200}, headers=manager)).json()) == 2
    # a sponsor login ignores the filter and only ever sees its own
    r = await client.get("/api/v1/campaigns", params={"sponsor_id": str(b.id)}, headers=shop)
    assert [c["name"] for c in r.json()] == ["Pesach sale"]

    r = await client.patch(f"/api/v1/campaigns/{cid}/status", json={"status": "approved"}, headers=manager)
    assert r.status_code == 200 and r.json()["status"] == "approved"

    for who in (dj, viewer):
        r = await client.get("/api/v1/campaigns", headers=who)
        assert r.status_code == 200 and len(r.json()) == 2
        r = await client.get(f"/api/v1/campaigns/{cid}", headers=who)
        assert r.status_code == 200 and r.json()["sponsor_name"] == "A Shop"
        drafts = await client.get(f"/api/v1/campaigns/{cid}/drafts", headers=who)
        assert drafts.status_code == 200 and drafts.json()[0]["script_text"] == "Brought to you by A Shop"
        comments = await client.get(f"/api/v1/campaigns/{cid}/comments", headers=who)
        assert comments.status_code == 200 and comments.json()[0]["body"] == "Please confirm the dates"
        for method, path, body in [
            ("POST", "/api/v1/campaigns", {"name": "x", "sponsor_id": str(a.id)}),
            ("PATCH", f"/api/v1/campaigns/{cid}", {"name": "x"}),
            ("PATCH", f"/api/v1/campaigns/{cid}/status", {"status": "active"}),
            ("POST", f"/api/v1/campaigns/{cid}/drafts", {"script_text": "x"}),
            ("POST", f"/api/v1/campaigns/{cid}/comments", {"body": "x"}),
        ]:
            r = await client.request(method, path, json=body, headers=who)
            assert r.status_code == 403, (method, path, r.status_code)
    assert (await client.get(f"/api/v1/campaigns/{cid}", headers=manager)).json()["status"] == "approved"
