"""GET /listeners/today values (B-LISTENERS-9): total sessions, unique listeners,
minutes, listening now and the peak, checked number by number."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.listener_session import ListenerSession
from app.models.station import Station, StationType


@pytest.mark.asyncio
async def test_today_numbers(client, db_session, auth_headers):
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now - midnight < timedelta(minutes=5):
        pytest.skip("too close to midnight for a fixed day")
    a = Station(id=uuid.uuid4(), name="A", type=StationType.INTERNET, timezone="UTC")
    b = Station(id=uuid.uuid4(), name="B", type=StationType.INTERNET, timezone="UTC")
    db_session.add_all([a, b])
    await db_session.commit()
    base = max(midnight + timedelta(seconds=1), now - timedelta(hours=2))

    def s(st, key, ip, start, end, dur, ended=None):
        return ListenerSession(station_id=st.id, session_key=key, ip_address=ip, started_at=start,
                               last_heartbeat=end, duration_seconds=dur, ended_at=ended)

    db_session.add_all([
        # Started last night, still listening: in the peak and live count, not in today's totals
        s(a, "night", "9.9.9.9", midnight - timedelta(hours=1), now, 3600),
        # Today: three overlapping at base+15..20 (plus the night owl = 4 at once)
        s(a, "x", "1.1.1.1", base, base + timedelta(seconds=30), 1800, ended=base + timedelta(seconds=30)),
        s(a, "y", "1.1.1.2", base + timedelta(seconds=10), base + timedelta(seconds=20), 600),
        s(b, "z", "1.1.1.1", base + timedelta(seconds=15), base + timedelta(seconds=25), 600),  # same person, other station
        # Listening now on station B
        s(b, "now", "1.1.1.3", now - timedelta(seconds=30), now, 30),
        # Yesterday entirely: counts nowhere
        s(a, "old", "8.8.8.8", midnight - timedelta(hours=5), midnight - timedelta(hours=4), 3600),
    ])
    await db_session.commit()

    t = (await client.get("/api/v1/listeners/today", headers=auth_headers)).json()
    assert t["date"] == now.strftime("%Y-%m-%d")
    assert t["total_sessions"] == 4                   # x, y, z, now
    assert t["unique_listeners"] == 3                 # 1.1.1.1 twice, .2, .3
    assert t["total_minutes"] == round((1800 + 600 + 600 + 30) / 60, 1) == 50.5
    assert t["active_now"] == 2                       # night owl + now
    assert t["peak_today"] == 4

    # Sessions that only touch end to start are not "at once"
    db_session.add_all([
        s(a, "t1", "2.2.2.1", base + timedelta(seconds=40), base + timedelta(seconds=50), 10),
        s(a, "t2", "2.2.2.2", base + timedelta(seconds=50), base + timedelta(seconds=60), 10),
    ])
    await db_session.commit()
    t = (await client.get("/api/v1/listeners/today", headers=auth_headers)).json()
    assert t["total_sessions"] == 6 and t["unique_listeners"] == 5
    assert t["peak_today"] == 4
