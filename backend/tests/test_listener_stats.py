"""Listener statistics (checklist V2-118..V2-120)."""
import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.v1 import listeners
from app.models.listener_session import ListenerSession
from app.models.station import Station, StationType
from tests.timing import limit

API = "/api/v1/listeners"


async def _station(db):
    st = Station(id=uuid.uuid4(), name="KBR", type=StationType.INTERNET)
    db.add(st)
    await db.commit()
    return st


@pytest.fixture
def geo_calls(monkeypatch):
    calls = []

    async def fake_geo(ip):
        calls.append(ip)
        return {"country": "US", "region": "NJ", "city": "Lakewood"}

    monkeypatch.setattr(listeners, "_resolve_geo", fake_geo)
    return calls


@pytest.mark.asyncio
async def test_heartbeat_ip_location_and_timeout(client, db_session, auth_headers, geo_calls):
    """V2-118/V2-120: IP from X-Forwarded-For first hop, else X-Real-IP; location
    stored; a session without a heartbeat for 90 s drops from the live count."""
    st = await _station(db_session)
    r = await client.post(f"{API}/heartbeat", json={"station_id": str(st.id), "session_key": "a"},
                          headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"})
    assert r.json() == {"status": "created"}
    await client.post(f"{API}/heartbeat", json={"station_id": str(st.id), "session_key": "b"},
                      headers={"X-Real-IP": "203.0.113.5"})
    t0 = time.perf_counter()
    r = await client.post(f"{API}/heartbeat", json={"station_id": str(st.id), "session_key": "a"})
    assert time.perf_counter() - t0 < limit(0.3)
    assert r.json()["status"] == "ok"
    assert geo_calls == ["198.51.100.7", "203.0.113.5"]  # repeat heartbeats do no lookup

    rows = {s.session_key: s for s in (await db_session.execute(select(ListenerSession))).scalars()}
    assert rows["a"].ip_address == "198.51.100.7" and rows["a"].city == "Lakewood"

    live = (await client.get(f"{API}/live", headers=auth_headers)).json()
    assert live["total_listeners"] == 2
    rows["b"].last_heartbeat = datetime.now(UTC) - timedelta(seconds=91)
    await db_session.commit()
    live = (await client.get(f"{API}/live", headers=auth_headers)).json()
    assert live["total_listeners"] == 1
    assert live["stations"][0]["regions"][0]["city"] == "Lakewood"


@pytest.mark.asyncio
async def test_today_peak_never_below_current(client, db_session, auth_headers, geo_calls, monkeypatch):
    """V2-119: peak = most sessions at once, never below the live count."""
    st = await _station(db_session)
    # Pinned to midday: run within seconds of midnight, "today" turned over mid-test (wall-clock sweep 2026-11-19)
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(listeners, "datetime", _Pinned)
    start = now - timedelta(hours=2)
    db_session.add_all([
        ListenerSession(station_id=st.id, session_key="x", ip_address="1.1.1.1", started_at=start,
                        last_heartbeat=start + timedelta(seconds=30), duration_seconds=30),
        ListenerSession(station_id=st.id, session_key="y", ip_address="1.1.1.2", started_at=start + timedelta(seconds=10),
                        last_heartbeat=start + timedelta(seconds=40), duration_seconds=30),
        ListenerSession(station_id=st.id, session_key="z", ip_address="1.1.1.2", started_at=start + timedelta(seconds=35),
                        last_heartbeat=start + timedelta(seconds=50), duration_seconds=15),
    ])
    await db_session.commit()
    t = (await client.get(f"{API}/today", headers=auth_headers)).json()
    assert t["total_sessions"] == 3 and t["unique_listeners"] == 2 and t["total_minutes"] == 1.2
    assert t["peak_today"] == 2 and t["active_now"] == 0


@pytest.mark.asyncio
async def test_first_heartbeat_does_not_wait_for_geo(client, db_session, monkeypatch):
    st = await _station(db_session)

    async def slow_geo(ip):
        await asyncio.sleep(1.0)
        return {"country": "US", "region": "", "city": ""}

    monkeypatch.setattr(listeners, "_resolve_geo", slow_geo)
    t0 = time.perf_counter()
    await client.post(f"{API}/heartbeat", json={"station_id": str(st.id), "session_key": "new"},
                      headers={"X-Forwarded-For": "198.51.100.8"})
    assert time.perf_counter() - t0 < limit(0.3)


@pytest.mark.asyncio
async def test_today_is_station_local(client, db_session, auth_headers, geo_calls):
    st = Station(id=uuid.uuid4(), name="NY", type=StationType.INTERNET, timezone="America/New_York")
    db_session.add(st)
    await db_session.commit()
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    now = datetime.now(UTC)
    utc_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_midnight = now.astimezone(ny).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    # Pick a session the two day boundaries disagree about
    if local_midnight < utc_midnight:
        when = local_midnight + timedelta(minutes=1)  # New York "today", but yesterday in UTC
    else:
        when = utc_midnight + timedelta(minutes=1)  # UTC "today", but yesterday in New York
    db_session.add(ListenerSession(station_id=st.id, session_key="ny", ip_address="1.1.1.9", started_at=when,
                                   last_heartbeat=when, duration_seconds=60))
    await db_session.commit()
    expected = 1 if when.astimezone(ny).date() == now.astimezone(ny).date() else 0
    t = (await client.get(f"{API}/today", headers=auth_headers)).json()
    assert t["total_sessions"] == expected


@pytest.mark.asyncio
async def test_spoofed_forwarded_for_is_not_trusted(client, db_session, geo_calls):
    st = await _station(db_session)
    # The proxy appends the real address; the first hop came from the client itself
    await client.post(f"{API}/heartbeat", json={"station_id": str(st.id), "session_key": "s"},
                      headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.77"})
    s = (await db_session.execute(select(ListenerSession))).scalar_one()
    assert s.ip_address == "203.0.113.77"
