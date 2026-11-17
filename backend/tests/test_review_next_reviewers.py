"""Two reviewers working one review queue with /next.

Rows: B-REVIEWS-3, B-REVIEWS-4, B-REVIEWS-5."""
import uuid

import pytest

from app.core.security import create_access_token
from app.models.asset import Asset
from app.models.user import User, UserRole

API = "/api/v1/reviews"


async def _user(db, role):
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=True)
    db.add(u)
    await db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


@pytest.mark.asyncio
async def test_two_reviewers_never_get_the_same_item(client, auth_headers, db_session, admin_user):
    """B-REVIEWS-5: /next gives each reviewer the next pending item in order and assigns it to
    them; the item a reviewer holds is not offered to the other; the claim keeps the item's
    version so the reviewer's decision (version 1) saves. A sponsor gets 403."""
    assets = [Asset(id=uuid.uuid4(), title=f"Song {i}", file_path=f"{i}.mp3", asset_type="music", duration=180)
              for i in range(3)]
    db_session.add_all(assets)
    await db_session.commit()
    q = (await client.post(f"{API}/queues", json={"name": "Batch", "asset_ids": [str(a.id) for a in assets]},
                           headers=auth_headers)).json()
    dj, dj_headers = await _user(db_session, UserRole.DJ)
    _, sponsor_headers = await _user(db_session, UserRole.SPONSOR)

    mine = (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json()
    theirs = (await client.get(f"{API}/queues/{q['id']}/next", headers=dj_headers)).json()
    assert mine["asset_id"] == str(assets[0].id) and mine["assigned_to"] == str(admin_user.id)
    assert theirs["asset_id"] == str(assets[1].id) and theirs["assigned_to"] == str(dj.id)
    assert mine["status"] == theirs["status"] == "in_review"

    r = await client.patch(f"{API}/items/{theirs['id']}", json={"status": "approved", "version": 1}, headers=dj_headers)
    assert r.status_code == 200, r.text
    third = (await client.get(f"{API}/queues/{q['id']}/next", headers=dj_headers)).json()
    assert third["asset_id"] == str(assets[2].id)
    assert (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json() is None

    assert (await client.get(f"{API}/queues/{q['id']}/next", headers=sponsor_headers)).status_code == 403


@pytest.mark.asyncio
async def test_queue_and_items_reads(client, auth_headers, db_session):
    """B-REVIEWS-3 / B-REVIEWS-4: a queue reads back with its counts, its items in order;
    an unknown queue is 404 and has no items."""
    assets = [Asset(id=uuid.uuid4(), title=f"S{i}", file_path=f"{i}.mp3", asset_type="music", duration=180)
              for i in range(2)]
    db_session.add_all(assets)
    await db_session.commit()
    q = (await client.post(f"{API}/queues", json={"name": "Read", "asset_ids": [str(a.id) for a in assets]},
                           headers=auth_headers)).json()
    got = await client.get(f"{API}/queues/{q['id']}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["name"] == "Read" and got.json()["total_items"] == 2
    items = (await client.get(f"{API}/queues/{q['id']}/items", headers=auth_headers)).json()
    assert items["total"] == 2 and [i["asset_id"] for i in items["items"]] == [str(a.id) for a in assets]
    assert (await client.get(f"{API}/queues/{uuid.uuid4()}", headers=auth_headers)).status_code == 404
    empty = (await client.get(f"{API}/queues/{uuid.uuid4()}/items", headers=auth_headers)).json()
    assert empty == {"items": [], "total": 0}
