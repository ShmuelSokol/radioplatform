"""Listener club details (checklist V2-30..V2-35): taste profile labels, rating
rules, rankings, admin member list speed, deactivated members, raffles."""
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1.crm import _compute_taste_profile
from app.models.asset import Asset
from app.models.crm_member import CrmMember
from app.models.raffle import Raffle, RaffleEntry
from app.models.song_rating import SongRating

API = "/api/v1/crm"


async def _member(db, name="Moshe", pin="123456", active=True):
    m = CrmMember(id=uuid.uuid4(), name=name, pin=pin, is_active=active)
    db.add(m)
    await db.commit()
    return m


async def _rate(db, member, specs):
    """specs: list of (asset_type, category, rating, favourite)."""
    for kind, cat, rating, fav in specs:
        a = Asset(id=uuid.uuid4(), title=f"{kind}-{cat}-{uuid.uuid4().hex[:4]}", file_path="x.mp3",
                  asset_type=kind, category=cat, duration=100)
        db.add(a)
        db.add(SongRating(member_id=member.id, asset_id=a.id, rating=rating, is_favorite=fav))
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("specs,label", [
    ([], "The Newcomer"),
    ([("music", "lively", 5, True)] * 4, "The Newcomer"),
    ([("music", "lively", 5, True)] * 3 + [("music", "relax", 4, True)] * 2, "The Simcha Machine"),
    ([("music", "relax", 5, True)] * 3 + [("music", "lively", 4, True)] * 2, "The Heartfelt Chazzan"),
    ([("shiur", None, 5, False)] * 3 + [("music", "lively", 3, False)] * 2, "The Yeshiva Yungelight"),
    ([("music", "lively", 3, False)] * 2 + [("music", "relax", 3, False)] * 2 + [("music", "med_fast", 3, False)] * 2, "A True Harry"),
    ([("music", "lively", 3, False)] * 25 + [("music", "relax", 3, False)] * 6, "The Mixtape Maven"),
    ([("music", "lively", 3, False)] * 6, "The Music Lover"),
])
async def test_taste_profile_labels(db_session, specs, label):
    """V2-30"""
    m = await _member(db_session)
    await _rate(db_session, m, specs)
    profile = await _compute_taste_profile(db_session, m.id)
    assert profile.label == label
    assert profile.stats["total_ratings"] == len(specs)


@pytest.mark.asyncio
async def test_rating_rules(client, db_session):
    """V2-31: 1-5 only, re-rating updates, unknown song 404, removing nothing 404."""
    m = await _member(db_session)
    pin = {"X-CRM-PIN": m.pin}
    a1 = Asset(id=uuid.uuid4(), title="One", artist="A", file_path="1.mp3", asset_type="music", duration=100)
    a2 = Asset(id=uuid.uuid4(), title="Two", artist="B", file_path="2.mp3", asset_type="music", duration=100)
    db_session.add_all([a1, a2])
    await db_session.commit()

    for bad in (0, 6):
        r = await client.post(f"{API}/rate", json={"asset_id": str(a1.id), "rating": bad}, headers=pin)
        assert r.status_code == 422
    t0 = time.perf_counter()
    r = await client.post(f"{API}/rate", json={"asset_id": str(a1.id), "rating": 3}, headers=pin)
    assert time.perf_counter() - t0 < 0.3
    assert r.status_code == 200 and r.json()["asset_title"] == "One"
    r = await client.post(f"{API}/rate", json={"asset_id": str(a1.id), "rating": 5, "is_favorite": True}, headers=pin)
    assert r.json()["rating"] == 5
    await client.post(f"{API}/rate", json={"asset_id": str(a2.id), "rating": 4}, headers=pin)
    mine = (await client.get(f"{API}/my-ratings", headers=pin)).json()
    assert len(mine) == 2  # no duplicate for One
    assert {x["asset_title"]: x["rating"] for x in mine} == {"One": 5, "Two": 4}
    assert {x["asset_artist"] for x in mine} == {"A", "B"}

    r = await client.post(f"{API}/rate", json={"asset_id": str(uuid.uuid4()), "rating": 3}, headers=pin)
    assert r.status_code == 404
    assert (await client.delete(f"{API}/rate/{uuid.uuid4()}", headers=pin)).status_code == 404
    assert (await client.delete(f"{API}/rate/{a2.id}", headers=pin)).status_code == 204
    assert len((await client.get(f"{API}/my-ratings", headers=pin)).json()) == 1


@pytest.mark.asyncio
async def test_rankings_and_deactivated_members(client, auth_headers, db_session):
    """V2-32/V2-34: ranking by average then count, rounded; a deactivated member's PIN
    is refused but their ratings still count and their raffle entry can still win."""
    m1 = await _member(db_session, "A", "111111")
    m2 = await _member(db_session, "B", "222222")
    m3 = await _member(db_session, "C", "333333")
    top = Asset(id=uuid.uuid4(), title="Top", file_path="t.mp3", asset_type="music", duration=100)
    many = Asset(id=uuid.uuid4(), title="Many", file_path="m.mp3", asset_type="music", duration=100)
    db_session.add_all([top, many])
    db_session.add_all([
        SongRating(member_id=m1.id, asset_id=top.id, rating=5, is_favorite=True),
        SongRating(member_id=m1.id, asset_id=many.id, rating=5),
        SongRating(member_id=m2.id, asset_id=many.id, rating=4, is_favorite=True),
        SongRating(member_id=m3.id, asset_id=many.id, rating=5),
    ])
    await db_session.commit()

    ranks = (await client.get(f"{API}/song-rankings", headers=auth_headers)).json()
    assert [r["title"] for r in ranks] == ["Top", "Many"]  # one 5-star outranks 4.67 from three
    assert ranks[1]["avg_rating"] == 4.67 and ranks[1]["total_ratings"] == 3 and ranks[1]["favorite_count"] == 1

    raffle = Raffle(id=uuid.uuid4(), title="Prize", starts_at=datetime.now(UTC) - timedelta(days=1),
                    ends_at=datetime.now(UTC) - timedelta(minutes=1), status="open")
    db_session.add(raffle)
    db_session.add(RaffleEntry(raffle_id=raffle.id, member_id=m2.id))
    await db_session.commit()

    assert (await client.delete(f"{API}/members/{m2.id}", headers=auth_headers)).status_code == 204
    assert (await client.post(f"{API}/login", json={"pin": "222222"})).status_code == 401
    r = await client.post(f"{API}/rate", json={"asset_id": str(top.id), "rating": 1}, headers={"X-CRM-PIN": "222222"})
    assert r.status_code == 401
    ranks = (await client.get(f"{API}/song-rankings", headers=auth_headers)).json()
    assert ranks[1]["total_ratings"] == 3  # deactivated member's rating still counts

    # A raffle past its end is still "open" and can be drawn; the only entrant wins
    r = await client.post(f"{API}/raffles/{raffle.id}/draw", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "drawn" and r.json()["winner_name"] == "B"
    assert (await client.post(f"{API}/raffles/{raffle.id}/draw", headers=auth_headers)).status_code == 400


@pytest.mark.asyncio
async def test_member_list_pin_search_and_speed(client, auth_headers, db_session):
    """V2-33: managers see PINs; search by name only; only active members; a page
    of 200 members loads within S-4 (300 ms server time; 1.5 s allowed on SQLite in CI)."""
    members = [CrmMember(id=uuid.uuid4(), name=f"Member {i:03d}", pin=f"{i:06d}", phone=f"+1555{i:07d}")
               for i in range(200)]
    members.append(CrmMember(id=uuid.uuid4(), name="Gone", pin="999999", is_active=False))
    db_session.add_all(members)
    await db_session.commit()

    t0 = time.perf_counter()
    r = await client.get(f"{API}/members", params={"limit": 200}, headers=auth_headers)
    took = time.perf_counter() - t0
    body = r.json()
    assert body["total"] == 200 and len(body["members"]) == 200
    assert all(m["pin"] for m in body["members"])
    assert "Gone" not in {m["name"] for m in body["members"]}
    assert took < 1.5, f"member page of 200 took {took:.2f}s"

    assert (await client.get(f"{API}/members", params={"search": "Member 01"}, headers=auth_headers)).json()["total"] == 10
    assert (await client.get(f"{API}/members", params={"search": "+1555"}, headers=auth_headers)).json()["total"] == 0


@pytest.mark.asyncio
async def test_raffle_dates_and_entry(client, auth_headers, db_session):
    """V2-35: end before start is refused; active = open and inside its window;
    entering twice is refused."""
    now = datetime.now(UTC)
    r = await client.post(f"{API}/raffles", json={
        "title": "Backwards", "starts_at": now.isoformat(), "ends_at": (now - timedelta(hours=1)).isoformat(),
    }, headers=auth_headers)
    assert r.status_code == 422

    live = Raffle(id=uuid.uuid4(), title="Live", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    later = Raffle(id=uuid.uuid4(), title="Later", starts_at=now + timedelta(hours=1), ends_at=now + timedelta(hours=2))
    db_session.add_all([live, later])
    await db_session.commit()
    m = await _member(db_session)
    active = (await client.get(f"{API}/raffles/active")).json()
    assert [x["title"] for x in active] == ["Live"]
    pin = {"X-CRM-PIN": m.pin}
    assert (await client.post(f"{API}/raffles/{live.id}/enter", headers=pin)).status_code == 201
    assert (await client.post(f"{API}/raffles/{live.id}/enter", headers=pin)).status_code == 409
    assert (await client.post(f"{API}/raffles/{later.id}/enter", headers=pin)).status_code == 400


@pytest.mark.xfail(strict=True, reason="BUG: raffle station_id is ignored; every active raffle shows on every station's Listen page")
@pytest.mark.asyncio
async def test_raffle_shows_only_on_its_station(client, db_session):
    from app.models.station import Station, StationType

    a = Station(id=uuid.uuid4(), name="A", type=StationType.INTERNET)
    b = Station(id=uuid.uuid4(), name="B", type=StationType.INTERNET)
    now = datetime.now(UTC)
    db_session.add_all([a, b])
    await db_session.commit()
    db_session.add(Raffle(title="A only", station_id=a.id, starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1)))
    await db_session.commit()
    shown = (await client.get(f"{API}/raffles/active", params={"station_id": str(b.id)})).json()
    assert shown == []
