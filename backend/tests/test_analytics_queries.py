"""Analytics figures (checklist V2-37..V2-40)."""
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.station import Station, StationType
from app.models.user import User, UserRole
from tests.timing import limit

API = "/api/v1/analytics"


async def _setup(db):
    a = Station(id=uuid.uuid4(), name="A", type=StationType.INTERNET, timezone="America/New_York")
    b = Station(id=uuid.uuid4(), name="B", type=StationType.INTERNET, timezone="America/New_York")
    song = Asset(id=uuid.uuid4(), title="Song", file_path="s.mp3", asset_type="music", category="lively", duration=600)
    bare = Asset(id=uuid.uuid4(), title="Bare", file_path="b.mp3", asset_type="music", category=None, duration=300)
    gone = Asset(id=uuid.uuid4(), title="Gone", file_path="g.mp3", asset_type="music", duration=60)
    db.add_all([a, b, song, bare, gone])
    await db.commit()
    return a, b, song, bare, gone


@pytest.mark.asyncio
async def test_summary_breakdown_and_limits(client, auth_headers, db_session):
    """V2-38/V2-40: totals, sources, uncategorized, deleted items count in totals
    but vanish from top lists; days 1-90, limit 1-100."""
    a, b, song, bare, gone = await _setup(db_session)
    now = datetime.now(UTC)
    db_session.add_all([
        PlayLog(station_id=a.id, asset_id=song.id, start_utc=now - timedelta(hours=1), source=PlaySource.SCHEDULER),
        PlayLog(station_id=a.id, asset_id=song.id, start_utc=now - timedelta(hours=2), source=PlaySource.MANUAL),
        PlayLog(station_id=a.id, asset_id=bare.id, start_utc=now - timedelta(hours=3), source=PlaySource.SCHEDULER),
        PlayLog(station_id=b.id, asset_id=gone.id, start_utc=now - timedelta(hours=4), source=PlaySource.SCHEDULER),
        PlayLog(station_id=a.id, asset_id=song.id, start_utc=now - timedelta(days=10), source=PlaySource.SCHEDULER),
    ])
    await db_session.commit()
    await db_session.delete(gone)  # play_log.asset_id is set null / row kept
    await db_session.commit()

    t0 = time.perf_counter()
    s = (await client.get(f"{API}/summary", params={"days": 7}, headers=auth_headers)).json()
    assert time.perf_counter() - t0 < limit(0.3)
    assert s["total_plays"] == 4
    assert s["avg_plays_per_day"] == round(4 / 7, 1)  # divided by days asked
    assert s["total_airtime_hours"] == round((600 + 600 + 300) / 3600, 1)
    assert sum(s["plays_by_source"].values()) == 4

    only_a = (await client.get(f"{API}/summary", params={"station_id": str(a.id)}, headers=auth_headers)).json()
    assert only_a["total_plays"] == 3 and only_a["unique_assets"] == 2

    top = (await client.get(f"{API}/top-assets", headers=auth_headers)).json()["assets"]
    assert [t["title"] for t in top] == ["Song", "Bare"]  # the deleted item is gone from the list
    cats = {(c["category"], c["asset_type"]): c["play_count"]
            for c in (await client.get(f"{API}/category-breakdown", headers=auth_headers)).json()["categories"]}
    assert cats == {("lively", "music"): 2, ("uncategorized", "music"): 1}

    for bad in ({"days": 0}, {"days": 91}):
        assert (await client.get(f"{API}/summary", params=bad, headers=auth_headers)).status_code == 422
    for bad in ({"limit": 0}, {"limit": 101}):
        assert (await client.get(f"{API}/top-assets", params=bad, headers=auth_headers)).status_code == 422
    assert (await client.get(f"{API}/summary")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_airtime_counts_what_aired(client, auth_headers, db_session):
    a, _, song, _, _ = await _setup(db_session)
    start = datetime.now(UTC) - timedelta(hours=1)
    db_session.add(PlayLog(station_id=a.id, asset_id=song.id, start_utc=start, end_utc=start + timedelta(seconds=360),
                           source=PlaySource.MANUAL))  # skipped after 6 of its 10 minutes
    await db_session.commit()
    s = (await client.get(f"{API}/summary", headers=auth_headers)).json()
    assert s["total_airtime_hours"] == 0.1


@pytest.mark.asyncio
async def test_hours_are_station_local(client, auth_headers, db_session):
    a, _, song, _, _ = await _setup(db_session)
    # 20:30 New York (EST, UTC-5) = 01:30 UTC next day
    now = datetime.now(UTC)
    when = (now - timedelta(days=1)).replace(hour=1, minute=30, second=0, microsecond=0)
    if when.month in (3, 4, 5, 6, 7, 8, 9, 10):
        when = when.replace(hour=0)  # EDT: 20:30 local = 00:30 UTC
    db_session.add(PlayLog(station_id=a.id, asset_id=song.id, start_utc=when, source=PlaySource.SCHEDULER))
    await db_session.commit()
    hours = (await client.get(f"{API}/hourly-distribution", params={"station_id": str(a.id)}, headers=auth_headers)).json()["hours"]
    assert hours == [{"hour": 20, "plays": 1}]


@pytest.mark.asyncio
async def test_station_limited_manager_cannot_read_other_station(client, db_session):
    a, b, song, _, _ = await _setup(db_session)
    m = User(id=uuid.uuid4(), email="m@a.test", hashed_password=hash_password("managerpw1"), role=UserRole.MANAGER,
             is_active=True, station_ids=[str(a.id)])
    db_session.add(m)
    await db_session.commit()
    tok = (await client.post("/api/v1/auth/login", json={"email": "m@a.test", "password": "managerpw1"})).json()["access_token"]
    r = await client.get(f"{API}/summary", params={"station_id": str(b.id)}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
