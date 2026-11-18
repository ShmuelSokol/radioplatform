"""Review queues (checklist V2-24..V2-29): a review decision is the library item's
review status, progress counts, claims, history and comments."""
import time
import uuid

import pytest
from sqlalchemy import select

from app.models.asset import Asset, fit_for_random_pick, not_placeholder
from tests.timing import limit

API = "/api/v1/reviews"


async def _assets(db_session, n=3):
    items = [
        Asset(id=uuid.uuid4(), title=f"Song {i}", file_path=f"assets/{i}.mp3", asset_type="music", duration=180)
        for i in range(n)
    ]
    db_session.add_all(items)
    await db_session.commit()
    return items


async def _queue(client, headers, assets):
    r = await client.post(
        f"{API}/queues", json={"name": "Batch 1", "asset_ids": [str(a.id) for a in assets]}, headers=headers
    )
    assert r.status_code == 201, r.text
    q = r.json()
    items = (await client.get(f"{API}/queues/{q['id']}/items", headers=headers)).json()["items"]
    return q, items


@pytest.mark.asyncio
async def test_reject_takes_the_song_off_automatic_picks(client, auth_headers, db_session):
    """V2-24: Reject sets the library item to rejected and rotations stop drawing it;
    Approve brings it back. Flag and Skip leave the item's status alone."""
    a, b, c = await _assets(db_session)
    q, items = await _queue(client, auth_headers, [a, b, c])

    t0 = time.perf_counter()
    r = await client.patch(f"{API}/items/{items[0]['id']}", json={"status": "rejected", "version": 1}, headers=auth_headers)
    assert time.perf_counter() - t0 < limit(0.5)
    assert r.status_code == 200
    await client.patch(f"{API}/items/{items[1]['id']}", json={"status": "approved", "version": 1}, headers=auth_headers)
    await client.patch(f"{API}/items/{items[2]['id']}", json={"status": "flagged", "version": 1}, headers=auth_headers)

    for x in (a, b, c):
        await db_session.refresh(x)
    assert (a.review_status, b.review_status, c.review_status) == ("rejected", "approved", "pending")

    pickable = set((await db_session.execute(select(Asset.id).where(fit_for_random_pick()))).scalars())
    assert a.id not in pickable and {b.id, c.id} <= pickable
    named = set((await db_session.execute(select(Asset.id).where(not_placeholder()))).scalars())
    assert a.id not in named

    # Changing one's mind: approve after reject puts it back
    r = await client.patch(f"{API}/items/{items[0]['id']}", json={"status": "approved", "version": 2}, headers=auth_headers)
    assert r.status_code == 200
    await db_session.refresh(a)
    assert a.review_status == "approved"
    pickable = set((await db_session.execute(select(Asset.id).where(fit_for_random_pick()))).scalars())
    assert a.id in pickable


@pytest.mark.asyncio
async def test_batch_reject_sets_library_status(client, auth_headers, db_session):
    a, b = await _assets(db_session, 2)
    q, items = await _queue(client, auth_headers, [a, b])
    r = await client.post(
        f"{API}/queues/{q['id']}/batch-update",
        json={"item_ids": [i["id"] for i in items], "status": "rejected"}, headers=auth_headers,
    )
    assert r.json() == {"updated": 2}
    for x in (a, b):
        await db_session.refresh(x)
        assert x.review_status == "rejected"


@pytest.mark.asyncio
async def test_stale_version_is_refused(client, auth_headers, db_session):
    """V2-25 (server side): the second save with an old version gets 409."""
    (a,) = await _assets(db_session, 1)
    _, items = await _queue(client, auth_headers, [a])
    r1 = await client.patch(f"{API}/items/{items[0]['id']}", json={"status": "approved", "version": 1}, headers=auth_headers)
    r2 = await client.patch(f"{API}/items/{items[0]['id']}", json={"status": "rejected", "version": 1}, headers=auth_headers)
    assert r1.status_code == 200 and r1.json()["version"] == 2
    assert r2.status_code == 409 and r2.json()["detail"] == "Item was modified by another user"
    await db_session.refresh(a)
    assert a.review_status == "approved"


@pytest.mark.asyncio
async def test_queue_progress_and_audit(client, auth_headers, db_session):
    """V2-26: each decision recounts; open -> in_progress -> completed; batch recounts,
    ignores items of another queue and writes no audit entry."""
    a, b, c, d = await _assets(db_session, 4)
    q, items = await _queue(client, auth_headers, [a, b, c])
    q2, other_items = await _queue(client, auth_headers, [d])
    assert q["status"] == "open" and q["total_items"] == 3 and q["reviewed_items"] == 0

    await client.patch(f"{API}/items/{items[0]['id']}", json={"status": "skipped", "notes": "later", "version": 1}, headers=auth_headers)
    got = (await client.get(f"{API}/queues/{q['id']}", headers=auth_headers)).json()
    assert got["status"] == "in_progress" and got["reviewed_items"] == 1

    r = await client.post(
        f"{API}/queues/{q['id']}/batch-update",
        json={"item_ids": [items[1]["id"], items[2]["id"], other_items[0]["id"]], "status": "approved"},
        headers=auth_headers,
    )
    assert r.json() == {"updated": 2}
    got = (await client.get(f"{API}/queues/{q['id']}", headers=auth_headers)).json()
    assert got["status"] == "completed" and got["reviewed_items"] == 3
    still = (await client.get(f"{API}/queues/{q2['id']}/items", headers=auth_headers)).json()["items"]
    assert still[0]["status"] == "pending"

    activity = (await client.get(f"{API}/queues/{q['id']}/activity", headers=auth_headers)).json()
    assert [x["action_type"] for x in activity] == ["skipped"]  # batch wrote none
    assert activity[0]["comment"] == "later" and activity[0]["details"] == {"version": 2}
    assert activity[0]["user_email"] == "testadmin@test.com"


@pytest.mark.asyncio
async def test_next_claims_first_pending(client, auth_headers, db_session, admin_user):
    """V2-27: /next claims the first pending item; empty queue -> null."""
    a, b = await _assets(db_session, 2)
    q, items = await _queue(client, auth_headers, [a, b])
    n1 = (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json()
    assert n1["id"] == items[0]["id"] and n1["status"] == "in_review"
    assert n1["assigned_to"] == str(admin_user.id)
    n2 = (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json()
    assert n2["id"] == items[1]["id"]
    assert (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json() is None


@pytest.mark.asyncio
async def test_abandoned_claim_is_offered_again(client, auth_headers, db_session):
    """A claim with no save for 30 minutes expires and the item is offered again;
    a fresh claim is not handed to anyone else."""
    from datetime import UTC, datetime, timedelta

    from app.models.review_queue import ReviewItem

    (a,) = await _assets(db_session, 1)
    q, items = await _queue(client, auth_headers, [a])
    await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)  # reviewer closes the tab
    assert (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json() is None  # 0 min: still held

    item = await db_session.get(ReviewItem, uuid.UUID(items[0]["id"]))
    item.updated_at = datetime.now(UTC) - timedelta(minutes=29)
    await db_session.commit()
    assert (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json() is None  # 29 min: still held

    item.updated_at = datetime.now(UTC) - timedelta(minutes=31)
    await db_session.commit()
    again = (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json()
    assert again is not None and again["id"] == items[0]["id"] and again["status"] == "in_review"
    # the new claim starts its own 30 minutes
    assert (await client.get(f"{API}/queues/{q['id']}/next", headers=auth_headers)).json() is None


@pytest.mark.asyncio
async def test_history_and_comments(client, auth_headers, db_session):
    """V2-28: history newest first with reviewer email; empty comment accepted."""
    (a,) = await _assets(db_session, 1)
    _, items = await _queue(client, auth_headers, [a])
    r = await client.post(f"{API}/assets/{a.id}/comment", json={"comment": ""}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "created"
    await client.patch(f"{API}/items/{items[0]['id']}", json={"status": "approved", "version": 1}, headers=auth_headers)
    hist = (await client.get(f"{API}/assets/{a.id}/history", headers=auth_headers)).json()
    assert {h["action_type"] for h in hist} == {"comment", "approved"}
    assert all(h["user_email"] == "testadmin@test.com" for h in hist)
    stamps = [h["created_at"] for h in hist]
    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.asyncio
async def test_bad_asset_id_is_400(client, auth_headers, db_session):
    """V2-29: a malformed id is a clear 400, not a server error; total fixed at creation."""
    r = await client.post(f"{API}/queues", json={"name": "x", "asset_ids": ["not-a-uuid"]}, headers=auth_headers)
    assert r.status_code == 400
    (a,) = await _assets(db_session, 1)
    q, _ = await _queue(client, auth_headers, [a])
    assert q["total_items"] == 1
