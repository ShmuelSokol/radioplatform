"""The queue page's times for timed items that share one time.

KBR Jerusalem 2026-11-19: Shabbos is 311 five-minute silences, all due at candle
lighting. Every one was shown at that minute and the Motzei Shabbos opening after
them showed a Friday time though it airs Saturday night.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def nothing(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)


def _asset(db, title, kind, duration):
    a = Asset(id=uuid.uuid4(), title=title, duration=float(duration), asset_type=kind,
              file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(a)
    return a


def _shown(body, title):
    return [datetime.fromisoformat(r["estimated_start"]) for r in body["entries"]
            if (r.get("asset") or {}).get("title") == title]


@pytest.mark.asyncio
async def test_items_due_at_one_time_follow_each_other(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(station)
    song = _asset(db_session, "Before", "music", 900)  # cut by the blackout
    silence = _asset(db_session, "Silence (5 min)", "silence", 300)
    after = _asset(db_session, "Motzei Shabbos", "music", 200)
    await db_session.flush()
    now = datetime.now(UTC)
    start = now + timedelta(minutes=10)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                              status="pending", source="auto"))
    for i in range(12):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id,
                                  position=2 + i, status="pending", source="blackout", preempt_at=start))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=20,
                              status="pending", source="auto"))
    await db_session.commit()

    r = await client.get(f"/api/v1/stations/{station.id}/queue?limit=50", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    silences = sorted(_shown(body, "Silence (5 min)"))
    assert len(silences) == 12
    for i, t in enumerate(silences):
        assert abs((t - start).total_seconds() - i * 300) < 5, (i, t)
    (opening,) = _shown(body, "Motzei Shabbos")
    assert abs((opening - start).total_seconds() - 12 * 300) < 5


@pytest.mark.asyncio
async def test_an_overdue_hard_item_is_shown_now_not_in_the_past(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db_session.add(station)
    time_check = _asset(db_session, "Time", "time", 10)
    song = _asset(db_session, "Song", "music", 200)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=1,
                              status="pending", source="time", preempt_at=now - timedelta(seconds=30)))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                              status="pending", source="auto"))
    await db_session.commit()
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    (shown,) = _shown(body, "Time")
    assert (shown - now).total_seconds() > -3
    (s,) = _shown(body, "Song")
    assert abs((s - now).total_seconds() - 10) < 5
