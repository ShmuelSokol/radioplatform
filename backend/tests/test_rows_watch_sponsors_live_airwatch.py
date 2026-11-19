"""Checklist rows on the on-air self-check (app/services/air_watch.py) and alert folding.

F-AIRWATCH-1, -15, -16, -24, -26, -32, V1-63, V3-54. Every time is pinned (a fixed
`now` handed to the watch, a fixed monotonic clock); nothing waits on the wall clock.
"""
import asyncio
import types
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.alert import Alert, AlertSeverity, AlertType
from app.models.station import Station
from app.models.user import UserRole
from app.services import air_watch
from app.services.air_watch import AirWatch, Finding
from tests.test_air_watch import _asset, _entry, _station
from tests.test_station_permissions import _user

HOUR = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)  # 9 PM New York


def _pushes(monkeypatch):
    from app.config import settings
    from app.services import bug_report_service

    sent = []

    async def fake_push(message, title, click="", tags="", priority="default"):
        sent.append((title, message))
        return True

    monkeypatch.setattr(bug_report_service, "send_ntfy", fake_push)
    monkeypatch.setattr(settings, "NTFY_TOPIC", "test-topic-not-real")
    return sent


async def _alerts(db):
    return (await db.execute(select(Alert).execution_options(populate_existing=True))).scalars().all()


# ── F-AIRWATCH-1: the loop ───────────────────────────────────────────────────


async def _drive_loop(monkeypatch, leader_by_pass, fail_passes=()):
    """Run watch_loop with a fake clock: returns the sleeps asked for and the passes run."""
    sleeps, ran = [], []
    passes = iter(leader_by_pass)
    current = {"leader": next(passes)}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:  # after each pass, the next pass's leadership
            try:
                current["leader"] = next(passes)
            except StopIteration:
                raise asyncio.CancelledError from None

    monkeypatch.setattr(air_watch, "asyncio", types.SimpleNamespace(
        sleep=fake_sleep, CancelledError=asyncio.CancelledError, gather=asyncio.gather))

    from app.services import scheduler_engine

    monkeypatch.setattr(scheduler_engine, "get_scheduler",
                        lambda: types.SimpleNamespace(is_leader=current["leader"]))

    @asynccontextmanager
    async def fake_session():
        yield object()

    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "async_session_factory", fake_session)
    watch = AirWatch()

    async def fake_run_once(db, now=None):
        ran.append(len(sleeps))
        if len(ran) in fail_passes:
            raise RuntimeError("database gone")
        return []

    monkeypatch.setattr(watch, "run_once", fake_run_once)
    monkeypatch.setattr(air_watch, "_watch", watch)
    with pytest.raises(asyncio.CancelledError):
        await air_watch.watch_loop()
    return sleeps, ran


@pytest.mark.asyncio
async def test_loop_waits_30s_then_runs_every_5s_only_with_the_lease(monkeypatch):
    """F-AIRWATCH-1: first pass 30 s after start, then one every 5 s, and only in passes
    where this process holds the scheduler lease."""
    sleeps, ran = await _drive_loop(monkeypatch, [True, True, False, False, True])
    assert sleeps[0] == 30
    assert all(s == air_watch.CHECK_EVERY_SECONDS == 5.0 for s in sleeps[1:])
    # passes 1, 2 and 5 hold the lease; 3 and 4 do not run
    assert ran == [1, 2, 5]


@pytest.mark.asyncio
async def test_a_failed_pass_does_not_stop_the_loop(monkeypatch):
    """F-AIRWATCH-1: a pass that raises is logged and the next one still runs."""
    sleeps, ran = await _drive_loop(monkeypatch, [True, True, True], fail_passes=(1,))
    assert ran == [1, 2, 3]


def test_loop_is_started_with_the_app():
    """F-AIRWATCH-1: main.py starts watch_loop in every process; the lease check inside
    decides which one actually checks."""
    import inspect

    from app import main

    assert "watch_loop()" in inspect.getsource(main)


# ── F-AIRWATCH-15: memory gone after a restart or lease move ─────────────────


@pytest.mark.asyncio
async def test_after_a_restart_problems_are_reported_again_into_the_open_alert(db_session, monkeypatch):
    """F-AIRWATCH-15: a new AirWatch (restart or lease move) reports a problem still there
    again; it folds into the open alert (repeats 2, one row), the push limit starts over,
    and an hour already checked is checked again while within 10 min."""
    sent = _pushes(monkeypatch)
    st = await _station(db_session)
    song = await _asset(db_session, "Song", duration=3600)
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    now = HOUR + timedelta(seconds=50)
    db_session.add_all([
        _entry(st, tc, status="played", started_at=HOUR - timedelta(hours=1)),
        _entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=30)),
    ])
    await db_session.commit()

    first = AirWatch()
    assert [f.kind for f in await first.run_once(db_session, now)] == ["hour_missing"]
    # the same process does not check the hour again
    assert await first.run_once(db_session, now + timedelta(seconds=30)) == []
    assert len(sent) == 1

    restarted = AirWatch()  # fresh memory
    found = await restarted.run_once(db_session, now + timedelta(minutes=5))
    assert [f.kind for f in found] == ["hour_missing"]
    alerts = await _alerts(db_session)
    assert len(alerts) == 1, "the repeat must fold into the open alert"
    assert alerts[0].context["repeats"] == 2
    assert len(sent) == 2, "the push limit starts over after a restart"

    # past 10 minutes after the hour the restarted watch no longer looks at it
    late = AirWatch()
    assert await late.run_once(db_session, HOUR + timedelta(minutes=10, seconds=1)) == []


# ── F-AIRWATCH-16 and -32: GET /air-watch ────────────────────────────────────


@pytest.mark.asyncio
async def test_process_without_the_lease_shows_no_runs(client, auth_headers, monkeypatch):
    """F-AIRWATCH-16: a process that never held the lease never ran a pass, so its
    GET /air-watch answers no runs and no findings."""
    sleeps, ran = await _drive_loop(monkeypatch, [False, False, False])
    assert ran == []
    fresh = air_watch._watch  # the watch the loop above used, never run
    monkeypatch.setattr(air_watch, "_watch", fresh)
    from app.api.v1 import air_watch as route

    monkeypatch.setattr(route, "get_watch", lambda: fresh)
    r = await client.get("/api/v1/air-watch", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"last_run": None, "runs": 0, "findings": []}


@pytest.mark.asyncio
async def test_status_open_to_every_staff_login_with_every_station(client, db_session, monkeypatch):
    """F-AIRWATCH-32: GET /air-watch is open to every staff role (not sponsors), and a
    login limited to one station still sees findings of other stations."""
    mine = Station(id=uuid.uuid4(), name="Mine", timezone="UTC")
    other = Station(id=uuid.uuid4(), name="Not mine", timezone="UTC")
    db_session.add_all([mine, other])
    await db_session.commit()
    watch = AirWatch()
    watch.recent.appendleft({"kind": "dead_air", "station_id": str(other.id), "station_name": "Not mine",
                             "title": "Nothing on air", "detail": "", "at": HOUR.isoformat(), "key": "k"})
    from app.api.v1 import air_watch as route

    monkeypatch.setattr(route, "get_watch", lambda: watch)
    for role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.DJ, UserRole.VIEWER):
        _, headers = await _user(client, db_session, role, station_ids=[str(mine.id)])
        r = await client.get("/api/v1/air-watch", headers=headers)
        assert r.status_code == 200, role
        assert [f["station_id"] for f in r.json()["findings"]] == [str(other.id)], role
    _, sponsor = await _user(client, db_session, UserRole.SPONSOR)
    assert (await client.get("/api/v1/air-watch", headers=sponsor)).status_code == 403


# ── F-AIRWATCH-24: what the Alerts page and the Errors tab read ───────────────


@pytest.mark.asyncio
async def test_findings_are_alerts_of_their_station_with_type_and_severity(client, auth_headers, db_session):
    """F-AIRWATCH-24: each finding is an open alert on its station: dead air as silence,
    the rest as playback gap; serious kinds critical, the others warning. The control
    room Errors tab reads GET /alerts?is_resolved=false&limit=100&station_id=<station>
    (SidePanels.tsx ErrorsPanel) and the Alerts page GET /alerts."""
    st = await _station(db_session)
    other = Station(id=uuid.uuid4(), name="Other", timezone="UTC")
    db_session.add(other)
    await db_session.commit()
    sid = str(st.id)
    watch = AirWatch()
    await watch.report(db_session, [
        Finding("dead_air", sid, "Main", "Nothing on air", "d", key="dead_air:1"),
        Finding("late_timed", sid, "Main", "Time has not started", "d", key="late:1"),
        Finding("overrun", sid, "Main", "Song shown on air 40 s past its end", "d", key="over:1"),
        Finding("timed_skipped", sid, "Main", "Weather was skipped", "d", key="skip:1"),
    ])
    await db_session.commit()
    r = await client.get("/api/v1/alerts", params={"is_resolved": "false", "limit": 100, "station_id": sid},
                         headers=auth_headers)
    assert r.status_code == 200
    got = {a["title"]: (a["alert_type"], a["severity"], a["station_id"]) for a in r.json()["alerts"]}
    assert got == {
        "Main: Nothing on air": ("silence", "critical", sid),
        "Main: Time has not started": ("playback_gap", "critical", sid),
        "Main: Song shown on air 40 s past its end": ("playback_gap", "warning", sid),
        "Main: Weather was skipped": ("playback_gap", "warning", sid),
    }
    r = await client.get("/api/v1/alerts", params={"station_id": str(other.id), "is_resolved": "false"},
                         headers=auth_headers)
    assert r.json()["alerts"] == []
    r = await client.get("/api/v1/alerts", headers=auth_headers)
    assert len(r.json()["alerts"]) == 4


# ── F-AIRWATCH-26: memory stays bounded ──────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_stays_bounded(db_session, monkeypatch):
    """F-AIRWATCH-26: reported keys trimmed past 5,000 (to 2,000), recent findings kept
    to 200, hours checked kept for 3 hours."""
    async def no_alert(*a, **kw):
        return None

    from app.services import alert_service

    monkeypatch.setattr(alert_service, "create_alert", no_alert)
    watch = AirWatch()
    sid = str(uuid.uuid4())
    await watch.report(db_session, [Finding("overrun", sid, "Main", "t", "d", key=f"k{i}")
                                    for i in range(5001)])
    assert len(watch.reported) == 2000
    assert len(watch.recent) == air_watch.RECENT_KEEP == 200

    # hours: a station checked every hour for 6 hours keeps only the last 3 hours' marks
    st = await _station(db_session)
    await db_session.commit()
    for h in range(6):
        await watch.check_station(db_session, st, HOUR + timedelta(hours=h, seconds=50))
    kept = sorted(watch.state(st.id).hours_checked)
    assert len(kept) == 4, kept  # the mark just checked and the three hours before it
    assert kept[0] == (HOUR + timedelta(hours=2)).astimezone(air_watch._tz(st)).isoformat()


# ── V1-63: "Station check failed" repeats every scheduler pass ───────────────


@pytest.mark.asyncio
async def test_station_check_failure_folds_into_one_alert(db_session, monkeypatch):
    """V1-63: a station whose check raises every 3 s pass gives one alert (repeats counted)
    and one notification, not one per pass. Passes run as the scheduler runs them:
    _check_all_stations, then the rollback that ends the pass."""
    from app.services import alert_service, scheduler_engine
    from app.services.scheduler_engine import SchedulerEngine, _safe_rollback

    st = Station(id=uuid.uuid4(), name="Broken", timezone="UTC", is_active=True)
    db_session.add(st)
    await db_session.commit()
    sent = []

    async def notify(db, alert):
        sent.append(alert.title)

    monkeypatch.setattr(alert_service, "_notify_users", notify)

    async def boom(self, db, station_id):
        raise RuntimeError("the queue read failed")

    monkeypatch.setattr(SchedulerEngine, "_advance_queue", boom)
    monkeypatch.setattr(scheduler_engine.settings.__class__, "engine_drives", lambda self, sid: False)
    engine = SchedulerEngine()
    for _ in range(10):
        await engine._check_all_stations(db_session)
        await _safe_rollback(db_session)
    rows = [a for a in await _alerts(db_session) if a.title == "Station check failed: Broken"]
    assert len(rows) == 1
    assert rows[0].context["repeats"] == 10
    assert sent == ["Station check failed: Broken"]


# ── V3-54: one outage, one alert, a few pushes, closes by itself ─────────────


@pytest.mark.asyncio
async def test_stream_server_outage_one_alert_one_push_and_it_closes(db_session, monkeypatch):
    """V3-54 (stream server outage): an hour of the stream server not answering, checked
    every 5 s, gives one alert and one push; when it answers in step again the alert is
    resolved by itself."""
    from app.config import settings
    from app.services import playout_engine

    sent = _pushes(monkeypatch)
    now = HOUR
    st = await _station(db_session)
    song = await _asset(db_session, "Song", duration=10_000)
    e = _entry(st, song, status="playing", started_at=now - timedelta(seconds=10))
    db_session.add(e)
    await db_session.commit()
    entry_id = str(e.id)
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: str(sid) == str(st.id))
    answer = {"value": None}

    async def status(_sid):
        return answer["value"]

    monkeypatch.setattr(playout_engine, "engine_status", status)
    clock = {"mono": 1000.0}
    monkeypatch.setattr(air_watch.time, "monotonic", lambda: clock["mono"])
    watch = AirWatch()
    for i in range(720):  # one hour of passes
        clock["mono"] = 1000.0 + 5 * i
        await watch.run_once(db_session, now + timedelta(seconds=5 * i))
    alerts = await _alerts(db_session)
    assert len(alerts) == 1 and "disagree" in alerts[0].title and not alerts[0].is_resolved
    assert len(sent) == 1

    answer["value"] = {"ok": True, "entry_id": entry_id, "title": "Song"}
    await watch.run_once(db_session, now + timedelta(hours=1))
    alerts = await _alerts(db_session)
    assert alerts[0].is_resolved is True and len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="found 2026-11-19: a dead-air alert ('Nothing on air') stays "
                   "open after music is back; only the drift alert closes by itself")
async def test_dead_air_alert_closes_when_music_is_back(db_session, monkeypatch):
    """V3-54 (dead air): one alert for the spell, and it closes when something plays again."""
    _pushes(monkeypatch)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    await db_session.commit()
    clock = {"mono": 1000.0}
    monkeypatch.setattr(air_watch.time, "monotonic", lambda: clock["mono"])
    watch = AirWatch()
    for i in range(60):
        clock["mono"] = 1000.0 + 5 * i
        await watch.run_once(db_session, HOUR + timedelta(seconds=5 * i))
    assert len(await _alerts(db_session)) == 1
    db_session.add(_entry(st, song, status="playing", started_at=HOUR + timedelta(seconds=300)))
    await db_session.commit()
    clock["mono"] += 5
    await watch.run_once(db_session, HOUR + timedelta(seconds=305))
    assert all(a.is_resolved for a in await _alerts(db_session))


def test_alert_kinds_used_here_exist():
    assert AlertType.SILENCE.value == "silence" and AlertSeverity.CRITICAL.value == "critical"
