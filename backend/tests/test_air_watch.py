"""On-air self-check (app/services/air_watch.py).

Main's 9 PM time check aired at 9:09 on 2026-11-16 and only Shmuel noticed. The watch
must catch exactly that: a hard item that is late or never starts, a missing time check
at the hour, dead air, an item shown past its end, a skipped time check, and an engine
that plays something other than what the site shows.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import air_watch
from app.services.air_watch import AirWatch


async def _station(db, **config):
    st = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", automation_config=config or {})
    db.add(st)
    await db.flush()
    return st


async def _asset(db, title, category=None, kind="music", duration=200.0):
    a = Asset(id=uuid.uuid4(), title=title, duration=duration, file_path=f"https://cdn.example.com/{title}.mp3",
              asset_type=kind, category=category)
    db.add(a)
    await db.flush()
    return a


def _entry(st, asset, **kw):
    kw.setdefault("position", 1)
    kw.setdefault("status", "pending")
    return QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=asset.id, **kw)


def _kinds(found):
    return sorted(f.kind for f in found)


@pytest.mark.asyncio
async def test_time_check_stuck_behind_a_shiur_is_reported(db_session):
    """The 9 PM case: the time check is due, a shiur is still on air, it has not started."""
    now = datetime.now(UTC)
    st = await _station(db_session)
    shiur = await _asset(db_session, "Daf Yomi", kind="shiur", duration=4140)
    tc = await _asset(db_session, "Time 21:00", category="time_announcement", kind="jingle", duration=8)
    db_session.add_all([
        _entry(st, shiur, status="playing", started_at=now - timedelta(minutes=60)),
        _entry(st, tc, position=2, preempt_at=now - timedelta(seconds=30), timing_mode="hard"),
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    late = [f for f in found if f.kind == "late_timed"]
    assert late, _kinds(found)
    assert "Time 21:00 has not started" in late[0].title
    assert "Daf Yomi" in late[0].detail


@pytest.mark.asyncio
async def test_time_check_that_aired_late_is_reported(db_session):
    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    due = now - timedelta(minutes=5)
    db_session.add_all([
        _entry(st, tc, status="played", preempt_at=due, timing_mode="hard", started_at=due + timedelta(seconds=40)),
        _entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=10)),
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert any(f.kind == "late_timed" and "40 s late" in f.title for f in found), found


@pytest.mark.asyncio
async def test_on_time_and_soft_items_are_not_reported(db_session):
    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    spot = await _asset(db_session, "Sponsor", kind="spot", duration=30)
    due = now - timedelta(minutes=2)
    db_session.add_all([
        _entry(st, tc, status="played", preempt_at=due, timing_mode="hard", started_at=due + timedelta(seconds=0.4)),
        # soft items wait for the song by design, so a wait is not a fault
        _entry(st, spot, position=3, preempt_at=now - timedelta(seconds=60), timing_mode="soft", source="ad_slot"),
        _entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=10)),
    ])
    await db_session.commit()
    assert await AirWatch().check_station(db_session, st, now) == []


@pytest.mark.asyncio
async def test_missing_hourly_time_check_is_reported(db_session):
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    # 50 s after a local hour; the station aired a time check last hour, none this hour
    hour = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)  # 9 PM New York
    now = hour + timedelta(seconds=50)
    db_session.add_all([
        _entry(st, tc, status="played", started_at=hour - timedelta(hours=1)),
        _entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=30)),
    ])
    await db_session.commit()
    watch = AirWatch()
    found = await watch.check_station(db_session, st, now)
    assert [f.kind for f in found] == ["hour_missing"], found
    assert "9 PM" in found[0].title
    # checked once per hour
    assert await watch.check_station(db_session, st, now + timedelta(seconds=5)) == []


@pytest.mark.asyncio
async def test_hourly_time_check_on_time_is_fine(db_session):
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    hour = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
    now = hour + timedelta(seconds=50)
    db_session.add_all([
        _entry(st, tc, status="played", started_at=hour - timedelta(hours=1)),
        _entry(st, tc, position=2, status="played", preempt_at=hour, timing_mode="hard",
               started_at=hour + timedelta(seconds=0.3)),
        _entry(st, song, position=3, status="playing", started_at=hour + timedelta(seconds=9)),
    ])
    await db_session.commit()
    assert await AirWatch().check_station(db_session, st, now) == []


@pytest.mark.asyncio
async def test_dead_air_after_twenty_seconds(db_session):
    now = datetime.now(UTC)
    st = await _station(db_session)
    await db_session.commit()
    watch = AirWatch()
    assert await watch.check_station(db_session, st, now, mono=1000.0) == []
    assert await watch.check_station(db_session, st, now, mono=1010.0) == []
    found = await watch.check_station(db_session, st, now, mono=1021.0)
    assert [f.kind for f in found] == ["dead_air"]


@pytest.mark.asyncio
async def test_no_dead_air_alarm_during_a_live_show(db_session):
    now = datetime.now(UTC)
    st = await _station(db_session, live_show_id=str(uuid.uuid4()))
    await db_session.commit()
    watch = AirWatch()
    await watch.check_station(db_session, st, now, mono=0.0)
    assert await watch.check_station(db_session, st, now, mono=100.0) == []


@pytest.mark.asyncio
async def test_item_shown_past_its_end(db_session):
    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song", duration=200)
    db_session.add(_entry(st, song, status="playing", started_at=now - timedelta(seconds=260)))
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert [f.kind for f in found] == ["overrun"]


@pytest.mark.asyncio
async def test_skipped_time_check_is_reported_but_not_a_duplicate(db_session):
    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    weather = await _asset(db_session, "Weather", category="weather_spot", kind="spot", duration=30)
    due = now - timedelta(minutes=3)
    db_session.add_all([
        # a duplicate of a time check that did air: fine
        _entry(st, tc, status="played", preempt_at=due, timing_mode="hard", started_at=due),
        _entry(st, tc, position=2, status="skipped", preempt_at=due, timing_mode="hard"),
        # weather skipped and never aired: reported
        _entry(st, weather, position=3, status="skipped", preempt_at=due, timing_mode="hard"),
        _entry(st, song, position=4, status="playing", started_at=now - timedelta(seconds=10)),
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert [(f.kind, f.title) for f in found] == [("timed_skipped", "Weather was skipped")]


@pytest.mark.asyncio
async def test_engine_drift(db_session, monkeypatch):
    from app.config import settings
    from app.services import playout_engine

    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    db_session.add(_entry(st, song, status="playing", started_at=now - timedelta(seconds=10)))
    await db_session.commit()
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: str(sid) == str(st.id))

    async def status(_sid):
        return {"ok": True, "entry_id": "something-else", "title": "Other song"}

    monkeypatch.setattr(playout_engine, "engine_status", status)
    watch = AirWatch()
    assert await watch.check_station(db_session, st, now, mono=0.0) == []
    found = await watch.check_station(db_session, st, now, mono=25.0)
    assert [f.kind for f in found] == ["engine_drift"]
    assert "Other song" in found[0].detail


@pytest.mark.asyncio
async def test_report_makes_one_alert_and_one_push_per_problem(db_session, monkeypatch):
    from sqlalchemy import select

    from app.config import settings
    from app.models.alert import Alert
    from app.services import bug_report_service

    pushes = []

    async def fake_push(message, title, click="", tags="", priority="default"):
        pushes.append((title, message))
        return True

    monkeypatch.setattr(bug_report_service, "send_ntfy", fake_push)
    monkeypatch.setattr(settings, "NTFY_TOPIC", "test-topic-not-real")
    now = datetime.now(UTC)
    st = await _station(db_session)
    shiur = await _asset(db_session, "Daf Yomi", kind="shiur", duration=4140)
    tc = await _asset(db_session, "Time 21:00", category="time_announcement", kind="jingle", duration=8)
    db_session.add_all([
        _entry(st, shiur, status="playing", started_at=now - timedelta(minutes=60)),
        _entry(st, tc, position=2, preempt_at=now - timedelta(seconds=30), timing_mode="hard"),
    ])
    await db_session.commit()
    watch = AirWatch()
    await watch.run_once(db_session, now)
    await watch.run_once(db_session, now + timedelta(seconds=5))
    alerts = (await db_session.execute(select(Alert))).scalars().all()
    assert len(alerts) == 1 and "Time 21:00 has not started" in alerts[0].title
    assert len(pushes) == 1 and "Main" in pushes[0][0]
    assert watch.recent and watch.recent[0]["kind"] == "late_timed"


@pytest.mark.asyncio
async def test_status_endpoint(client, auth_headers):
    air_watch.get_watch().recent.clear()
    r = await client.get("/api/v1/air-watch", headers=auth_headers)
    assert r.status_code == 200
    assert set(r.json()) == {"last_run", "runs", "findings"}
    assert (await client.get("/api/v1/air-watch")).status_code in (401, 403)
