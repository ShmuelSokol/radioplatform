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
async def test_no_hour_alarm_while_hard_start_is_off(db_session):
    """HS off: the time check waits for the song by design (station_modes.py)."""
    from app.services.station_modes import HS_KEY, hard_start_on

    st = await _station(db_session, **{HS_KEY: False})
    assert not hard_start_on(st)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    hour = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
    now = hour + timedelta(seconds=50)
    db_session.add_all([
        _entry(st, tc, status="played", started_at=hour - timedelta(hours=1)),
        _entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=30)),
    ])
    await db_session.commit()
    assert await AirWatch().check_station(db_session, st, now) == []


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
async def test_time_check_cut_by_panic_is_not_called_never_aired(db_session):
    """F-AIRWATCH-28: a time check that started and was cut is reported as cut short."""
    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    due = now - timedelta(minutes=2)
    db_session.add_all([
        _entry(st, tc, status="skipped", preempt_at=due, timing_mode="hard", started_at=due + timedelta(seconds=1)),
        _entry(st, song, position=2, status="playing", started_at=due + timedelta(seconds=3)),
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert [(f.kind, f.title) for f in found] == [("timed_skipped", "Time was cut short")]
    assert "never aired" not in found[0].detail


@pytest.mark.asyncio
async def test_time_check_replaced_by_another_recording_is_not_skipped(db_session):
    """F-AIRWATCH-29: the time check aired from a different recording (a block's own, a
    redrawn weather spot): the replaced one is not reported as skipped."""
    now = datetime.now(UTC)
    st = await _station(db_session)
    old_tc = await _asset(db_session, "Time old", category="time_announcement", kind="jingle", duration=8)
    new_tc = await _asset(db_session, "Time new", category="time_announcement", kind="jingle", duration=8)
    song = await _asset(db_session, "Song")
    due = now - timedelta(minutes=2)
    db_session.add_all([
        _entry(st, old_tc, status="skipped", preempt_at=due, timing_mode="hard"),
        _entry(st, new_tc, position=2, status="played", preempt_at=due, timing_mode="hard", started_at=due),
        _entry(st, song, position=3, status="playing", started_at=due + timedelta(seconds=9)),
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert [f for f in found if f.kind == "timed_skipped"] == []


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


@pytest.mark.asyncio
async def test_one_station_failing_does_not_stop_the_others(db_session, monkeypatch):
    """F-AIRWATCH-27: a database error on one station rolls back and the next station is
    still checked (on Postgres the error would otherwise abort the session for all)."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    bad = await _station(db_session)
    good = Station(id=uuid.uuid4(), name="Jerusalem", timezone="Asia/Jerusalem", automation_config={})
    db_session.add(good)
    await db_session.commit()
    bad_id, good_id = bad.id, good.id  # the rollback expires the test's own objects too
    real = AirWatch.check_station
    seen, checked = [], []

    async def flaky(self, db, station, now):
        seen.append(station.id)
        if station.id == bad_id:
            await db.execute(text("SELECT * FROM no_such_table"))
        result = await real(self, db, station, now)
        checked.append(station.id)
        return result

    monkeypatch.setattr(AirWatch, "check_station", flaky)
    monkeypatch.setattr(AirWatch, "report", lambda self, db, found: _noop())
    await AirWatch().run_once(db_session, now)
    assert set(seen) >= {bad_id, good_id}
    assert checked == [good_id], "the station after the failing one was not checked"


async def _noop():
    return None


# ── Checklist rows F-AIRWATCH-13, -17, -19, -20, -21, -25 ────────────────────


@pytest.mark.asyncio
async def test_alert_timing_limits(db_session, monkeypatch):
    """F-AIRWATCH-13: with a pass every 5 s, a late hard item is found by 8 s + one pass
    (13 s), a missing time check by hh:00:45, dead air by 25 s; the push goes out in the
    same pass as the alert (ntfy delivery to the phone itself is not tested)."""
    from app.config import settings
    from app.services import bug_report_service

    assert air_watch.LATE_SECONDS + air_watch.CHECK_EVERY_SECONDS <= 13
    assert air_watch.HOUR_CHECK_AFTER_SECONDS + air_watch.CHECK_EVERY_SECONDS <= 45
    assert air_watch.DEAD_AIR_SECONDS + air_watch.CHECK_EVERY_SECONDS <= 25

    # Late hard item: not at 7.9 s, yes at 8.1 s
    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    due = now - timedelta(seconds=1)
    db_session.add_all([
        _entry(st, song, status="playing", started_at=now - timedelta(seconds=30)),
        _entry(st, tc, position=2, preempt_at=due, timing_mode="hard"),
    ])
    await db_session.commit()
    watch = AirWatch()
    assert await watch.check_station(db_session, st, due + timedelta(seconds=7.9)) == []
    found = await watch.check_station(db_session, st, due + timedelta(seconds=8.1))
    assert [f.kind for f in found] == ["late_timed"]

    # The push is sent in the pass that writes the alert
    pushes = []

    async def fake_push(message, title, click="", tags="", priority="default"):
        pushes.append(title)
        return True

    monkeypatch.setattr(bug_report_service, "send_ntfy", fake_push)
    monkeypatch.setattr(settings, "NTFY_TOPIC", "test-topic-not-real")
    await AirWatch().run_once(db_session, due + timedelta(seconds=8.1))
    assert len(pushes) == 1

    # Missing time check: checked from hh:00:40, not before
    st2 = Station(id=uuid.uuid4(), name="Second", timezone="America/New_York", automation_config={})
    db_session.add(st2)
    hour = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
    db_session.add_all([
        _entry(st2, tc, status="played", started_at=hour - timedelta(hours=1)),
        _entry(st2, song, position=2, status="playing", started_at=hour + timedelta(seconds=10)),
    ])
    await db_session.commit()
    watch = AirWatch()
    assert await watch.check_station(db_session, st2, hour + timedelta(seconds=39.9)) == []
    found = await watch.check_station(db_session, st2, hour + timedelta(seconds=40))
    assert [f.kind for f in found] == ["hour_missing"]


async def _hung_server():
    """A stream server that takes the connection and never answers."""
    import asyncio

    async def hang(reader, writer):
        await asyncio.sleep(3600)

    server = await asyncio.start_server(hang, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_hung_stream_servers_keep_a_pass_under_five_seconds(db_session, monkeypatch):
    """F-AIRWATCH-17: engine stations whose stream server hangs. Enough of them that waiting
    for each in turn (3 s apiece) passes the limit, also where limit() is tripled (CI)."""
    import time as _time

    from app.config import settings
    from tests.timing import limit

    server, port = await _hung_server()
    try:
        stations = []
        for i in range(int(limit(5.0) // 3) + 1):
            st = Station(id=uuid.uuid4(), name=f"Engine {i}", timezone="America/New_York", automation_config={})
            db_session.add(st)
            stations.append(st)
        await db_session.commit()
        monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
        monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", ",".join(str(s.id) for s in stations))
        monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "made-up-engine-token")
        assert all(settings.engine_drives(s.id) for s in stations)
        t0 = _time.perf_counter()
        await AirWatch().run_once(db_session)
        took = _time.perf_counter() - t0
    finally:
        server.close()
    assert took < limit(5.0), f"one pass took {took:.1f} s"


@pytest.mark.asyncio
async def test_blackout_is_quiet(db_session):
    """F-AIRWATCH-19: inside a blackout no late_timed, no hour_missing, no dead_air, and the
    Shabbos silence running past its length is not an overrun."""
    from app.models.holiday_window import HolidayWindow

    real_now = datetime.now(UTC)
    hour = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
    st = await _station(db_session)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", reason="shabbos", is_blackout=True,
                                 start_datetime=min(real_now, hour) - timedelta(days=1),
                                 end_datetime=max(real_now, hour) + timedelta(days=1),
                                 affected_stations={"station_ids": [str(st.id)]}))
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    db_session.add_all([
        _entry(st, tc, status="played", started_at=hour - timedelta(hours=1)),
        _entry(st, tc, position=2, preempt_at=real_now - timedelta(seconds=60), timing_mode="hard"),
    ])
    await db_session.commit()
    watch = AirWatch()
    # Nothing on air for a long time, a hard item a minute overdue
    assert await watch.check_station(db_session, st, real_now, mono=0.0) == []
    assert await watch.check_station(db_session, st, real_now, mono=100.0) == []
    # hh:00:50 with no time check
    assert await watch.check_station(db_session, st, hour + timedelta(seconds=50)) == []
    # The silence on air ran 10 minutes past its length
    silence = await _asset(db_session, "Silence", kind="silence", duration=3600)
    db_session.add(_entry(st, silence, position=3, status="playing", started_at=real_now - timedelta(seconds=4200)))
    await db_session.commit()
    assert await watch.check_station(db_session, st, real_now, mono=200.0) == []


@pytest.mark.asyncio
async def test_operator_removing_a_time_check_is_not_reported(client, auth_headers, db_session):
    """F-AIRWATCH-20. Decision: an operator taking a time check, weather or sponsorship out
    of the queue (Remove) is deliberate and raises nothing (the row is deleted, not marked
    skipped). Cutting one on air (Panic) is still reported as "cut short", a warning, next to
    Panic's own alert (test_time_check_cut_by_panic_is_not_called_never_aired)."""
    import app.api.v1.queue as queue_module

    async def nothing(*a, **k):
        return None

    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    weather = await _asset(db_session, "Weather", category="weather_spot", kind="spot", duration=30)
    spot = await _asset(db_session, "Sponsor", kind="spot", duration=30)
    due = now + timedelta(seconds=30)
    removed = [
        _entry(st, tc, position=2, preempt_at=due, timing_mode="hard"),
        _entry(st, weather, position=3, preempt_at=due, timing_mode="hard"),
        _entry(st, spot, position=4, preempt_at=due, timing_mode="soft", source="ad_slot"),
    ]
    db_session.add_all([_entry(st, song, status="playing", started_at=now - timedelta(seconds=10)), *removed])
    await db_session.commit()
    original = queue_module._replenish_queue
    queue_module._replenish_queue = nothing
    try:
        for e in removed:
            r = await client.delete(f"/api/v1/stations/{st.id}/queue/{e.id}", headers=auth_headers)
            assert r.status_code == 204
    finally:
        queue_module._replenish_queue = original
    found = await AirWatch().check_station(db_session, st, due + timedelta(seconds=60))
    assert found == []


@pytest.mark.asyncio
async def test_wait_rule_time_check_behind_a_shiur_is_not_late(db_session, monkeypatch):
    """F-AIRWATCH-21: with the long-item rule "wait", the queue advance turns the time check
    soft before its time, so waiting for the shiur is not reported late."""
    import app.api.v1.queue as queue_module

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    now = datetime.now(UTC)
    st = await _station(db_session, timed_during_long_items="wait")
    shiur = await _asset(db_session, "Daf Yomi", kind="shiur", duration=3600)
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    due = now + timedelta(seconds=30)
    check = _entry(st, tc, position=2, preempt_at=due, timing_mode="hard")
    db_session.add_all([_entry(st, shiur, status="playing", started_at=now - timedelta(minutes=10)), check])
    await db_session.commit()
    await queue_module._check_advance(db_session, st.id)  # the scheduler pass before hh:00
    found = await AirWatch().check_station(db_session, st, due + timedelta(seconds=60))
    assert [f for f in found if f.kind == "late_timed"] == []


@pytest.mark.asyncio
async def test_eighty_stations_in_one_pass(db_session):
    """F-AIRWATCH-25: one pass over 80 stations takes under 5 s, with a bounded number of
    queries per station."""
    import time as _time

    from sqlalchemy import event

    from tests.conftest import engine as test_engine
    from tests.timing import limit

    # A fixed point 3 minutes after an hour whose time check aired on time, so the result
    # does not depend on when the suite runs (it failed in CI at 7:00:55 UTC)
    now = datetime.now(UTC).replace(minute=3, second=0, microsecond=0)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    for i in range(80):
        st = Station(id=uuid.uuid4(), name=f"Station {i}", timezone="America/New_York", automation_config={})
        db_session.add(st)
        db_session.add_all([
            _entry(st, tc, status="played", preempt_at=now - timedelta(minutes=3), timing_mode="hard",
                   started_at=now - timedelta(minutes=3)),
            _entry(st, tc, status="played", started_at=now - timedelta(hours=1)),
            _entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=30)),
            _entry(st, tc, position=3, preempt_at=now + timedelta(minutes=20), timing_mode="hard"),
        ])
    await db_session.commit()
    queries = []

    def count(*a, **k):
        queries.append(1)

    event.listen(test_engine.sync_engine, "before_cursor_execute", count)
    try:
        t0 = _time.perf_counter()
        found = await AirWatch().run_once(db_session, now)
        took = _time.perf_counter() - t0
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", count)
    assert found == []
    assert took < limit(5.0), f"one pass took {took:.2f} s"
    # At most 10 per station (the hour check included), plus a few shared ones per pass
    per_station = (len(queries) - 5) / 80
    assert per_station <= 10, f"{len(queries)} queries for 80 stations"


async def _soft_behind_shiur(db, *, left, waited, **config):
    now = datetime.now(UTC)
    st = await _station(db, **config)
    shiur = await _asset(db, "Yoma 79", kind="shiur", duration=49 * 60)
    weather = await _asset(db, "Weather", kind="spot", duration=40)
    db.add_all([
        _entry(st, shiur, status="playing", started_at=now - timedelta(seconds=49 * 60 - left)),
        _entry(st, weather, position=2, preempt_at=now - timedelta(seconds=waited), timing_mode="soft",
               source="block"),
    ])
    await db.commit()
    return st, now


@pytest.mark.asyncio
async def test_soft_item_that_should_have_paused_the_shiur_is_reported(db_session):
    """F-SOFTPAUSE-17: the 7:30 weather still waiting 2 min later behind a shiur that had
    19 min left at 7:30 is reported (it should have paused the shiur)."""
    st, now = await _soft_behind_shiur(db_session, left=19 * 60, waited=120)
    found = await AirWatch().check_station(db_session, st, now)
    assert [f.title for f in found if f.kind == "late_timed"] == ["Weather is waiting behind Yoma 79"]


@pytest.mark.asyncio
async def test_soft_item_waiting_for_a_nearly_finished_shiur_is_fine(db_session):
    """Under 5 min left at its time (4 min then, 2 min now): waiting is the rule, no alarm."""
    st, now = await _soft_behind_shiur(db_session, left=2 * 60, waited=120)
    assert [f for f in await AirWatch().check_station(db_session, st, now) if f.kind == "late_timed"] == []


@pytest.mark.asyncio
async def test_soft_item_waiting_on_a_wait_station_is_fine(db_session):
    st, now = await _soft_behind_shiur(db_session, left=19 * 60, waited=120, timed_during_long_items="wait")
    assert [f for f in await AirWatch().check_station(db_session, st, now) if f.kind == "late_timed"] == []


@pytest.mark.asyncio
async def test_soft_item_waiting_with_hs_off_is_fine(db_session):
    st, now = await _soft_behind_shiur(db_session, left=19 * 60, waited=120, hard_start=False)
    assert [f for f in await AirWatch().check_station(db_session, st, now) if f.kind == "late_timed"] == []


async def _sponsor_behind_story(db, *, with_weather=False):
    """All Day Stories (sponsorships_wait_for_talks): a story with 19 min left and a
    sponsorship due 2 min ago, alone or together with the weather."""
    now = datetime.now(UTC)
    st = await _station(db, sponsorships_wait_for_talks=True)
    story = await _asset(db, "Ufros Alenu", kind="stories", duration=49 * 60)
    spot = await _asset(db, "TEST SPONSOR 16 - Bagel Nosh", kind="spot", duration=21)   # clock-slot play (ad_slot)
    due = now - timedelta(seconds=120)
    entries = [_entry(st, story, status="playing", started_at=now - timedelta(seconds=30 * 60)),
               _entry(st, spot, position=2, preempt_at=due, timing_mode="soft", source="ad_slot")]
    if with_weather:
        weather = await _asset(db, "Weather", kind="spot", duration=40)
        entries.append(_entry(st, weather, position=3, preempt_at=due, timing_mode="soft", source="block"))
    db.add_all(entries)
    await db.commit()
    return st, now


@pytest.mark.asyncio
async def test_sponsorship_waiting_for_a_story_by_rule_is_fine(db_session):
    """All Day Stories: sponsorships alone wait for the story's end (PR 412), so the watch
    raises nothing (it raised "should have paused" every half hour on 2026-11-19)."""
    st, now = await _sponsor_behind_story(db_session)
    assert [f for f in await AirWatch().check_station(db_session, st, now) if f.kind == "late_timed"] == []


@pytest.mark.asyncio
async def test_weather_with_a_sponsorship_still_should_pause_the_story(db_session):
    """A group that is not sponsorships alone (weather rides with it) still pauses the
    story on that station, so waiting is still reported."""
    st, now = await _sponsor_behind_story(db_session, with_weather=True)
    found = [f.title for f in await AirWatch().check_station(db_session, st, now) if f.kind == "late_timed"]
    assert found and all("waiting behind Ufros Alenu" in t for t in found), found


@pytest.mark.asyncio
async def test_soft_item_just_due_is_given_time(db_session):
    st, now = await _soft_behind_shiur(db_session, left=19 * 60, waited=10)
    assert [f for f in await AirWatch().check_station(db_session, st, now) if f.kind == "late_timed"] == []


@pytest.mark.asyncio
async def test_drift_alert_clears_itself_once_in_step(db_session, monkeypatch):
    """An open "Stream server and site disagree" alert is resolved when the engine and the
    site agree again (Jerusalem had three left open after a droplet restart, 2026-11-17)."""
    from sqlalchemy import select

    from app.config import settings
    from app.models.alert import Alert
    from app.services import playout_engine
    from app.services.alert_service import create_alert

    now = datetime.now(UTC)
    st = await _station(db_session)
    song = await _asset(db_session, "Song")
    e = _entry(st, song, status="playing", started_at=now - timedelta(seconds=10))
    db_session.add(e)
    await db_session.commit()
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: str(sid) == str(st.id))
    other = Station(id=uuid.uuid4(), name="Other", timezone="America/New_York")
    db_session.add(other)
    await db_session.flush()
    for sid in (st.id, other.id):
        await create_alert(db_session, "playback_gap", "critical",
                           f"{st.name}: Stream server and site disagree", "x", station_id=sid)
    await db_session.commit()

    async def status(_sid):
        return {"ok": True, "entry_id": str(e.id), "title": "Song"}

    monkeypatch.setattr(playout_engine, "engine_status", status)
    assert await AirWatch().check_station(db_session, st, now, mono=0.0) == []
    await db_session.commit()
    rows = (await db_session.execute(select(Alert).execution_options(populate_existing=True))).scalars().all()
    by_station = {str(a.station_id): a for a in rows}
    assert by_station[str(st.id)].is_resolved is True
    assert by_station[str(st.id)].resolved_by is None
    assert "agree again" in by_station[str(st.id)].context["cleared_by_itself"]
    assert by_station[str(other.id)].is_resolved is False  # another station's stays open


@pytest.mark.asyncio
async def test_later_items_of_a_hard_block_are_not_late(db_session):
    """Main 2026-11-18 01:00Z: the 8 PM block is time check (6 s), promo (4 s), Daf Yomi.
    The shiur starts 10 s after the hour, exactly behind the two before it: not late.
    The same shiur starting 40 s after them is."""
    now = datetime.now(UTC)
    st = await _station(db_session)
    tc = await _asset(db_session, "Time 20:00", category="time_announcement", kind="jingle", duration=6)
    promo = await _asset(db_session, "Schedule promo", kind="jingle", duration=4)
    shiur = await _asset(db_session, "Daf Yomi Bechoros 60", kind="shiur", duration=3000)
    due = now - timedelta(minutes=3)
    third = _entry(st, shiur, position=3, status="playing", preempt_at=due, timing_mode="hard",
                   started_at=due + timedelta(seconds=10.5))
    db_session.add_all([
        _entry(st, tc, position=1, status="played", preempt_at=due, timing_mode="hard", started_at=due),
        _entry(st, promo, position=2, status="played", preempt_at=due, timing_mode="hard",
               started_at=due + timedelta(seconds=6.3)),
        third,
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert [f for f in found if f.kind == "late_timed"] == [], found

    third.started_at = due + timedelta(seconds=50)
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    late = [f for f in found if f.kind == "late_timed"]
    assert len(late) == 1 and "Daf Yomi Bechoros 60 aired 40 s late" in late[0].title, found


def test_recent_starts_and_due_items_have_indexes():
    """The self-check reads, every 5 s per station, what started or came due in the last
    10 minutes. Production 2026-11-18: with no index on started_at or preempt_at that
    read went through the station's whole queue history (2.4 to 3.9 s; 6,188 pages
    against 30 on a 400,000 row copy). Both indexes must be on the model (new
    databases) and in the list built in the background on the live one."""
    from app import main as app_main
    from app.models.queue_entry import QueueEntry

    on_model = {tuple(c.name for c in ix.columns) for ix in QueueEntry.__table__.indexes}
    assert ("station_id", "started_at") in on_model
    assert ("station_id", "preempt_at") in on_model
    assert app_main.CONCURRENT_INDEXES["ix_queue_entries_station_started"] == "queue_entries (station_id, started_at)"
    assert app_main.CONCURRENT_INDEXES["ix_queue_entries_station_preempt"] == "queue_entries (station_id, preempt_at)"


def test_waiting_and_playing_rows_have_their_own_indexes():
    """GET /now-playing's next due timed item, the timed-groups check and the row on air
    filter on status. Production 2026-11-18: 0.94 s, 3.6 s and 0.95 s, walking about 255k
    played rows to find about 1,850 waiting ones. Partial indexes on just those rows."""
    from app import main as app_main
    from app.models.queue_entry import QueueEntry

    ix = {i.name: i for i in QueueEntry.__table__.indexes}
    assert str(ix["ix_qe_pending_preempt"].dialect_options["postgresql"]["where"]) == "status = 'pending'"
    assert str(ix["ix_qe_playing_started"].dialect_options["postgresql"]["where"]) == "status = 'playing'"
    assert app_main.CONCURRENT_INDEXES["ix_qe_pending_preempt"] == (
        "queue_entries (station_id, preempt_at, position) WHERE status = 'pending'"
    )
    assert app_main.CONCURRENT_INDEXES["ix_qe_playing_started"] == (
        "queue_entries (station_id, started_at) WHERE status = 'playing'"
    )


@pytest.mark.asyncio
async def test_items_taken_out_for_shabbos_raise_nothing_during_it(db_session):
    """Inside a blackout the time checks, weather and sponsorships due then (or queued for
    before it) were taken out on purpose: no "was skipped" alert on Shabbos."""
    from app.models.holiday_window import HolidayWindow

    now = datetime.now(UTC)
    st = await _station(db_session)
    silence = await _asset(db_session, "Silence (5 min)", kind="silence", duration=300)
    tc = await _asset(db_session, "Time", category="time_announcement", kind="jingle", duration=8)
    sponsor = await _asset(db_session, "TEST SPONSOR 50", kind="spot", duration=30)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=now - timedelta(minutes=6),
                                 end_datetime=now + timedelta(hours=24), is_blackout=True, affected_stations=None))
    db_session.add_all([
        _entry(st, tc, status="skipped", preempt_at=now - timedelta(minutes=2), timing_mode="hard"),
        _entry(st, sponsor, position=2, status="skipped", source="block",
               preempt_at=now - timedelta(minutes=9), timing_mode="hard"),
        _entry(st, silence, position=3, status="playing", started_at=now - timedelta(seconds=40)),
    ])
    await db_session.commit()
    found = await AirWatch().check_station(db_session, st, now)
    assert found == []
