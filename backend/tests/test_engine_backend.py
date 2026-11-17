"""Engine-mode backend checks (checklist rows B-PLAYOUT-*, B-MODE-*, engine B-JOB-*).

The playout engine (infra/streaming/radio.liq) is faked: every command the backend sends
it is recorded instead of going over HTTP. Tests whose docstring starts with
"Reproduction" were strict xfails for engine bugs, fixed 2026-11-17
(wiki/streaming-architecture.md, "Engine gaps found by tests, fixed").
"""

import asyncio
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.config import settings
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry, resume_offset
from app.models.station import Station
from app.services import playout_engine
from tests.timing import limit

TOKEN = "made-up-engine-token"
HEADERS = {"X-Playout-Token": TOKEN}
# Section S of the checklist: operator and engine calls answer within 300 ms server time
# (checked as a median in test_engine_calls_stay_fast_with_a_long_queue) and 1 s at worst.
# A single call is held to the 1 s limit: on a shared test machine a disk flush or a
# garbage collection can stall one SQLite commit for a few hundred ms.
FAST_SECONDS = 1.0
MEDIAN_SECONDS = 0.3
_timings: list[float] = []


class Engine:
    """Records what the backend tells the engine; `ok` = False makes it fail to answer."""

    def __init__(self):
        self.sent: list[tuple[str, dict | None]] = []
        self.ok = True
        self.delay = 0.0

    async def command(self, path, payload=None, **_kw):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append((path, payload))
        return self.ok

    def paths(self):
        return [p for p, _ in self.sent]

    def timed(self):
        return [p for path, p in self.sent if path == "/timed"]


@pytest_asyncio.fixture
async def eng(db_session, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Engine FM", timezone="America/New_York")
    db_session.add(station)
    songs = []
    for i in range(4):
        songs.append(Asset(
            id=uuid.uuid4(), title=f"Song {i}", artist="Artist", duration=180.0,
            file_path=f"https://cdn.example.com/audio/{i}.mp3", asset_type="music",
        ))
    db_session.add_all(songs)
    await db_session.flush()
    entries = [
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=i + 1,
                   status="pending", source="auto")
        for i, a in enumerate(songs)
    ]
    db_session.add_all(entries)
    await db_session.commit()

    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    playout_engine.reset_state()

    engine = Engine()
    monkeypatch.setattr(playout_engine, "_command", engine.command)

    calls = {"replenish": 0, "broadcast": []}

    async def no_replenish(db, station_id):
        calls["replenish"] += 1

    async def record_broadcast(db, station_id, entry_id, *a, **k):
        calls["broadcast"].append(str(entry_id))

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", record_broadcast)
    # Warm up the lazy imports once (paid once per process in production), so the speed
    # checks below time the work of each call and not the first import
    await playout_engine.pick_next(db_session, station.id, set())
    yield station, entries, engine, calls
    playout_engine.reset_state()


async def _add(db, station, *, title, duration, kind="music", **entry_fields):
    asset = Asset(id=uuid.uuid4(), title=title, duration=duration, asset_type=kind,
                  file_path=f"https://cdn.example.com/audio/{uuid.uuid4().hex}.mp3")
    db.add(asset)
    await db.flush()
    entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id,
                       status=entry_fields.pop("status", "pending"),
                       source=entry_fields.pop("source", "auto"), **entry_fields)
    db.add(entry)
    await db.commit()
    return entry


async def _fresh(db, entry_id):
    res = await db.execute(
        select(QueueEntry).where(QueueEntry.id == entry_id).execution_options(populate_existing=True)
    )
    return res.scalar_one_or_none()


async def _on_air(client, station, entry_id, lane="main"):
    t0 = time.perf_counter()
    r = await client.post(f"/api/v1/playout/{station.id}/started", headers=HEADERS,
                          json={"entry_id": str(entry_id), "lane": lane})
    assert r.status_code == 200
    _timings.append(time.perf_counter() - t0)
    assert _timings[-1] < FAST_SECONDS
    return r.json()


async def _next(client, station, given=""):
    t0 = time.perf_counter()
    r = await client.get(f"/api/v1/playout/{station.id}/next?given={given}", headers=HEADERS)
    _timings.append(time.perf_counter() - t0)
    assert _timings[-1] < FAST_SECONDS
    return r


async def _statuses(db, entries):
    return {(await _fresh(db, e.id)).status for e in entries}


async def _alerts(db, station_id=None):
    q = select(Alert).execution_options(populate_existing=True)
    if station_id is not None:
        q = q.where(Alert.station_id == station_id)
    return (await db.execute(q)).scalars().all()


# ── B-PLAYOUT: the endpoints the engine calls ────────────────────────────────


@pytest.mark.asyncio
async def test_hello_forgets_what_the_engine_held(client, db_session, eng):
    """B-PLAYOUT-1/2: a (re)started engine holds nothing; the cued entry is handed again."""
    station, entries, _, _ = eng
    assert (await client.post(f"/api/v1/playout/{station.id}/hello")).status_code == 403
    assert (await client.post(f"/api/v1/playout/{uuid.uuid4()}/hello", headers=HEADERS)).status_code == 404

    first = (await _next(client, station)).json()
    assert first["entry_id"] == str(entries[0].id)
    state = playout_engine.get_state(station.id)
    state.timed_sent.add(str(entries[3].id))
    state.skip_requested = True
    state.hard_by_done.add("x")
    state.on_emergency = True

    t0 = time.perf_counter()
    r = await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert time.perf_counter() - t0 < limit(FAST_SECONDS)
    state = playout_engine.get_state(station.id)
    assert state.cued_entry_id is None and not state.timed_sent and not state.handed
    assert not state.skip_requested and not state.hard_by_done and not state.on_emergency
    assert state.last_contact > 0
    assert playout_engine.held_entry_ids(station.id) == set()
    # The engine lost its pre-loaded track: the same entry is handed to it again
    again = (await _next(client, station)).json()
    assert again["entry_id"] == str(entries[0].id)


@pytest.mark.asyncio
async def test_empty_queue_refills_once_then_204(client, db_session, eng, monkeypatch):
    """B-PLAYOUT-6: empty queue -> one refill attempt per ask, then 204; no refill in a blackout."""
    station, entries, _, calls = eng
    for e in entries:
        (await _fresh(db_session, e.id)).status = "played"
    await db_session.commit()

    r = await _next(client, station)
    assert r.status_code == 204
    assert calls["replenish"] == 1

    # The refill found something: it is handed out in the same call
    added = {}

    async def refill(db, station_id):
        calls["replenish"] += 1
        added["e"] = await _add(db, station, title="Fill", duration=200.0, position=1)

    monkeypatch.setattr(queue_module, "_replenish_queue", refill)
    r = await _next(client, station)
    assert r.status_code == 200 and r.json()["entry_id"] == str(added["e"].id)

    # Blackout: no refill attempt
    (await _fresh(db_session, added["e"].id)).status = "played"
    now = datetime.now(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", is_blackout=True,
                                 start_datetime=now - timedelta(hours=1),
                                 end_datetime=now + timedelta(hours=1)))
    await db_session.commit()
    before = calls["replenish"]
    r = await _next(client, station)
    assert r.status_code == 204
    assert calls["replenish"] == before


@pytest.mark.asyncio
async def test_entry_without_audio_is_dropped_and_next_handed(client, db_session, eng, monkeypatch):
    """B-PLAYOUT-9: an entry whose file cannot be reached is skipped, the next one handed."""
    station, entries, _, _ = eng
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    asset = await db_session.get(Asset, entries[0].asset_id)
    asset.file_path = "relative/no-storage.mp3"  # no public URL without storage configured
    await db_session.commit()
    r = await _next(client, station)
    assert r.json()["entry_id"] == str(entries[1].id)
    assert (await _fresh(db_session, entries[0].id)).status == "skipped"


@pytest.mark.asyncio
async def test_past_due_hard_time_check_is_next(client, db_session, eng):
    """B-PLAYOUT-10 / B-MODE-12 / B-JOB-20.

    Reproduction: hourly time check at hh:00 sits at the end of the queue by position
    (position 99, as the hourly entries are created hours ahead). The hand-over window
    was missed (backend restarted at :59:50 for 20 s, or the entry was created at
    hh:00:03), so it never went to the timed lane. When the engine asks for its next
    track at hh:00:05 it gets Song 0, then Song 1... The browser path
    (_pick_next_pending) picks every due timed item first."""
    station, entries, _, _ = eng
    clock = await _add(db_session, station, title="Time 9 PM", duration=8.0, kind="time",
                       position=99, timing_mode="hard",
                       preempt_at=datetime.now(UTC) - timedelta(seconds=5))
    r = await _next(client, station)
    assert r.json()["entry_id"] == str(clock.id)


@pytest.mark.asyncio
async def test_started_broadcasts_and_tops_up(client, db_session, eng):
    """B-PLAYOUT-15: a start report is broadcast at once and the queue topped up, but not
    in a blackout. B-MODE-17: the start report is the play log (>= 1 s)."""
    station, entries, _, calls = eng
    await _on_air(client, station, entries[0].id)
    assert calls["broadcast"] == [str(entries[0].id)]
    assert calls["replenish"] == 1

    row = await _fresh(db_session, entries[0].id)
    row.started_at = datetime.now(UTC) - timedelta(seconds=40)
    now = datetime.now(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Yom Tov", is_blackout=True,
                                 start_datetime=now - timedelta(minutes=1),
                                 end_datetime=now + timedelta(hours=1)))
    await db_session.commit()
    await _on_air(client, station, entries[1].id)
    assert calls["broadcast"] == [str(entries[0].id), str(entries[1].id)]
    assert calls["replenish"] == 1  # not during the blackout
    logs = (await db_session.execute(select(PlayLog).where(PlayLog.station_id == station.id))).scalars().all()
    assert [log.asset_id for log in logs] == [entries[0].asset_id]
    assert str(getattr(logs[0].source, "value", logs[0].source)) == "scheduler"


@pytest.mark.asyncio
async def test_engine_calls_stay_fast_with_a_long_queue(client, db_session, eng):
    """Speed (S-3): next / started stay under 300 ms with a day's worth of queue."""
    station, entries, _, _ = eng
    for i in range(300):
        await _add(db_session, station, title=f"Long {i}", duration=200.0, position=10 + i)
    given = []
    _timings.clear()
    for e in entries[:3]:
        r = await _next(client, station, ",".join(given))
        assert r.json()["entry_id"] == str(e.id)
        given.insert(0, str(e.id))
        await _on_air(client, station, e.id)
    assert statistics.median(_timings) < MEDIAN_SECONDS, _timings


# ── B-JOB: the scheduler's engine tick ───────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_ticks_the_engine_instead_of_advancing(db_session, eng, monkeypatch):
    """B-JOB-28 / B-MODE-1: engine station -> playout_engine.tick; browser station -> _advance_queue."""
    from app.services.scheduler_engine import SchedulerEngine

    station, _, _, _ = eng
    browser = Station(id=uuid.uuid4(), name="Browser FM", timezone="America/New_York")
    db_session.add(browser)
    await db_session.commit()
    ticked, advanced = [], []

    async def fake_tick(db, st):
        ticked.append(st.id)

    async def fake_advance(self, db, station_id):
        advanced.append(station_id)

    async def fake_check(self, db, st):
        return None

    monkeypatch.setattr(playout_engine, "tick", fake_tick)
    monkeypatch.setattr(SchedulerEngine, "_advance_queue", fake_advance)
    monkeypatch.setattr(SchedulerEngine, "_check_station", fake_check)
    await SchedulerEngine()._check_all_stations(db_session)
    assert ticked == [station.id]
    assert advanced == [browser.id]


@pytest.mark.asyncio
async def test_restart_at_59_50_still_hands_the_time_check(db_session, eng):
    """B-JOB-19 (engine, window not missed): the backend restarts at :59:50 and loses its
    memory; the first tick at :59:55 still hands the hh:00 time check over, on the second."""
    station, _, engine, _ = eng
    at = datetime.now(UTC) + timedelta(seconds=5)
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time",
                       position=50, timing_mode="hard", preempt_at=at)
    playout_engine.reset_state()  # the restart
    t0 = time.perf_counter()
    await playout_engine.tick(db_session, station)
    assert time.perf_counter() - t0 < limit(FAST_SECONDS)
    timed = engine.timed()
    assert [c["id"] for c in timed] == [str(clock.id)]
    assert abs(timed[0]["at"] - at.timestamp()) < 0.01

    # A second restart hands it again: the engine dedupes /timed by entry id (radio.liq timed_seen)
    playout_engine.reset_state()
    await playout_engine.tick(db_session, station)
    assert [c["id"] for c in engine.timed()] == [str(clock.id)] * 2


@pytest.mark.asyncio
async def test_missed_hand_over_still_airs_at_once(db_session, eng):
    """B-JOB-30 / B-JOB-19 (window missed). Reproduction: backend down from hh:59:10 to
    hh:00:02 (deploy), time check due hh:00:00, first tick at hh:00:02 -> no /timed sent;
    the song on air plays to its end and the time check then waits for its queue position."""
    station, _, engine, _ = eng
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time",
                       position=50, timing_mode="hard",
                       preempt_at=datetime.now(UTC) - timedelta(seconds=2))
    playout_engine.reset_state()
    await playout_engine.tick(db_session, station)
    assert [c["id"] for c in engine.timed()] == [str(clock.id)]


@pytest.mark.asyncio
async def test_failed_hand_over_is_retried_in_the_window(db_session, eng):
    """B-JOB-31 (retry): the engine did not answer /timed; the next tick tries again."""
    station, _, engine, _ = eng
    at = datetime.now(UTC) + timedelta(seconds=30)
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time",
                       position=50, timing_mode="hard", preempt_at=at)
    engine.ok = False
    await playout_engine.tick(db_session, station)
    assert str(clock.id) not in playout_engine.get_state(station.id).timed_sent
    engine.ok = True
    await playout_engine.tick(db_session, station)
    await playout_engine.tick(db_session, station)
    assert [c["id"] for c in engine.timed()] == [str(clock.id)] * 2  # one failed, one delivered
    assert str(clock.id) in {str(i) for i in playout_engine.held_entry_ids(station.id)}


@pytest.mark.asyncio
async def test_never_delivered_time_check_raises_an_alert(db_session, eng):
    """B-JOB-31 (alert). Reproduction: engine control URL answers 502 for the whole 45 s
    window before hh:00; at hh:00:01 nothing records that the time check was missed."""
    station, _, engine, _ = eng
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time",
                       position=50, timing_mode="hard",
                       preempt_at=datetime.now(UTC) + timedelta(seconds=20))
    engine.ok = False
    await playout_engine.tick(db_session, station)
    row = await _fresh(db_session, clock.id)
    row.preempt_at = datetime.now(UTC) - timedelta(seconds=1)  # its time came and went
    await db_session.commit()
    await playout_engine.tick(db_session, station)
    assert await _alerts(db_session, station.id)


@pytest.mark.asyncio
async def test_time_then_weather_go_over_as_one_group(db_session, eng):
    """B-MODE-10 / B-MODE-9: time check and weather share one time: handed in queue order
    with the same `at`, so radio.liq makes one take-over (group within 0.5 s)."""
    station, _, engine, _ = eng
    at = datetime.now(UTC) + timedelta(seconds=40)
    weather = await _add(db_session, station, title="Weather", duration=30.0, kind="weather",
                         position=61, timing_mode="hard", preempt_at=at)
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time",
                       position=60, timing_mode="hard", preempt_at=at)
    # Not yet inside the 45 s window: nothing sent
    for e in (weather, clock):
        (await _fresh(db_session, e.id)).preempt_at = at + timedelta(seconds=10)
    await db_session.commit()
    await playout_engine.tick(db_session, station)
    assert engine.timed() == []
    for e in (weather, clock):
        (await _fresh(db_session, e.id)).preempt_at = at
    await db_session.commit()
    await playout_engine.tick(db_session, station)
    timed = engine.timed()
    assert [c["id"] for c in timed] == [str(clock.id), str(weather.id)]
    assert timed[0]["at"] == timed[1]["at"] == at.timestamp()
    assert all('kbr_lane="timed"' in c["uri"] for c in timed)


@pytest.mark.asyncio
async def test_blackout_end_clears_silence_on_the_engine(client, db_session, eng):
    """B-JOB-33 / B-MODE-14: blackout over with silence on air -> the rest of the silence is
    dropped, the engine's pre-loaded track flushed and the silence skipped."""
    station, entries, engine, _ = eng
    silences = [
        await _add(db_session, station, title="Silence", duration=300.0, kind="silence", position=i)
        for i in (1, 2, 3)
    ]
    # Same asset for all silence entries, like fill_blackout_queue
    for s in silences[1:]:
        row = await _fresh(db_session, s.id)
        row.asset_id = silences[0].asset_id
    await db_session.commit()
    await _on_air(client, station, silences[0].id)

    # Still in the blackout: nothing happens
    now = datetime.now(UTC)
    window = HolidayWindow(id=uuid.uuid4(), name="Shabbos", is_blackout=True,
                           start_datetime=now - timedelta(hours=25), end_datetime=now + timedelta(minutes=5))
    db_session.add(window)
    await db_session.commit()
    await playout_engine.tick(db_session, station)
    assert "/skip" not in engine.paths()

    window.end_datetime = now - timedelta(seconds=1)
    await db_session.commit()
    t0 = time.perf_counter()
    await playout_engine.tick(db_session, station)
    assert time.perf_counter() - t0 < limit(FAST_SECONDS)
    assert engine.paths()[-2:] == ["/flush", "/skip"]
    for s in silences[1:]:
        assert (await _fresh(db_session, s.id)).status == "played"
    r = await _next(client, station)
    assert r.json()["entry_id"] == str(entries[0].id)


@pytest.mark.asyncio
async def test_blackout_end_leaves_no_silence_in_the_timed_lane(client, db_session, eng):
    """Reproduction: fill_blackout_queue gives every silence entry of an upcoming blackout
    preempt_at = start and no timing_mode, so all are hard-timed and the tick hands all of
    them to /timed 45 s before. The blackout is then shortened (or ends while silence
    remains). The tick sends /flush + /skip; radio.liq's timed lane stays open while its
    queue has items, so the next silence goes on air (one more per tick)."""
    station, _, engine, _ = eng
    # A current engine (radio.liq proto 2) announces itself; it understands /timed_drop
    r = await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS, json={"proto": 2})
    assert r.status_code == 200
    now = datetime.now(UTC)
    start = now + timedelta(seconds=20)
    first = await _add(db_session, station, title="Silence", duration=300.0, kind="silence",
                       position=1, preempt_at=start)
    silences = [first]
    for i in (2, 3):
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=first.asset_id, position=i,
                       status="pending", source="auto", preempt_at=start)
        db_session.add(e)
        silences.append(e)
    await db_session.commit()
    await playout_engine.tick(db_session, station)
    timed_lane = [c["id"] for c in engine.timed()]
    assert timed_lane == [str(s.id) for s in silences]  # all of them handed at once

    # The engine starts the first silence; then the (shortened) blackout is over
    await _on_air(client, station, silences[0].id, lane="timed")
    timed_lane.pop(0)
    await playout_engine.tick(db_session, station)
    assert "/skip" in engine.paths()
    # Model of radio.liq: /skip on the timed lane plays the next item of the timed queue,
    # except one the backend took back with /timed_drop (skipped the moment it would start)
    dropped = {i for path, p in engine.sent if path == "/timed_drop" for i in p["ids"]}
    timed_lane = [i for i in timed_lane if i not in dropped]
    on_air_next = timed_lane[0] if timed_lane else None
    assert on_air_next is None, "engine goes on with silence after the blackout ended"
    # Dropped before the skip, so the skip cannot start one of them; closed here as well
    assert engine.paths().index("/timed_drop") < engine.paths().index("/skip")
    for s in silences[1:]:
        assert (await _fresh(db_session, s.id)).status == "played"


@pytest.mark.asyncio
async def test_blackout_end_on_an_old_engine_sends_no_timed_drop(client, db_session, eng):
    """An engine with no `proto` has no /timed_drop: at blackout end it gets the old
    flush + skip only, and the silences still in its timed lane stay queued here."""
    station, _, engine, _ = eng
    start = datetime.now(UTC) + timedelta(seconds=20)
    first = await _add(db_session, station, title="Silence", duration=300.0, kind="silence",
                       position=1, preempt_at=start)
    second = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=first.asset_id, position=2,
                        status="pending", source="auto", preempt_at=start)
    db_session.add(second)
    await db_session.commit()
    await playout_engine.tick(db_session, station)
    assert [c["id"] for c in engine.timed()] == [str(first.id), str(second.id)]
    await _on_air(client, station, first.id, lane="timed")
    await playout_engine.tick(db_session, station)
    assert "/timed_drop" not in engine.paths()
    assert engine.paths()[-2:] == ["/flush", "/skip"]
    assert (await _fresh(db_session, second.id)).status == "pending"


@pytest.mark.asyncio
async def test_watchdog_alerts_once_per_half_hour(db_session, eng, monkeypatch):
    """B-JOB-36: no engine contact for > 120 s -> a critical alert, repeated at most every 30 min."""
    station, _, _, _ = eng
    clock = [100000.0]
    monkeypatch.setattr(playout_engine.time, "monotonic", lambda: clock[0])
    state = playout_engine.get_state(station.id)
    state.last_contact = clock[0] - 130
    await playout_engine.tick(db_session, station)
    alerts = await _alerts(db_session, station.id)
    assert [a.title for a in alerts] == ["Playout engine is not responding"]
    await playout_engine.tick(db_session, station)
    assert state.last_alert > 0
    first_alert = state.last_alert
    await playout_engine.tick(db_session, station)
    assert state.last_alert == first_alert  # not repeated within 30 min
    clock[0] += playout_engine.ALERT_REPEAT_SECONDS + 1
    await playout_engine.tick(db_session, station)
    assert state.last_alert > first_alert
    # Contact again: no alert
    state.last_contact = clock[0]
    state.last_alert = 0.0
    await playout_engine.tick(db_session, station)
    assert state.last_alert == 0.0


@pytest.mark.asyncio
async def test_watchdog_alerts_soon_after_host_boot(db_session, eng, monkeypatch):
    """Reproduction: this test VM's time.monotonic() was 1024 s; engine silent for 130 s ->
    tick -> no alert, because `mono - state.last_alert < ALERT_REPEAT_SECONDS` with last_alert 0."""
    station, _, _, _ = eng
    monkeypatch.setattr(playout_engine.time, "monotonic", lambda: 1000.0)
    playout_engine.get_state(station.id).last_contact = 1000.0 - 130
    await playout_engine.tick(db_session, station)
    assert await _alerts(db_session, station.id)


@pytest.mark.asyncio
async def test_watchdog_alerts_when_the_engine_never_called(db_session, eng, monkeypatch):
    """Reproduction: deploy the backend while the engine server is down; tick for 3 minutes;
    no 'not responding' alert, listeners on the stream hear nothing."""
    station, _, _, _ = eng
    clock = [100000.0]
    monkeypatch.setattr(playout_engine.time, "monotonic", lambda: clock[0])
    await playout_engine.tick(db_session, station)
    clock[0] += 180
    await playout_engine.tick(db_session, station)
    assert await _alerts(db_session, station.id)


@pytest.mark.asyncio
async def test_watchdog_quiet_while_a_long_item_plays(client, db_session, eng, monkeypatch):
    """Reproduction: engine asks /next and reports the start of a 30 min shiur, then (correctly)
    says nothing; 3 minutes later the tick creates a critical alert."""
    station, _, _, _ = eng
    clock = [100000.0]
    monkeypatch.setattr(playout_engine.time, "monotonic", lambda: clock[0])
    shiur = await _add(db_session, station, title="Shiur", duration=1800.0, kind="shiur", position=0)
    await _next(client, station)
    await _on_air(client, station, shiur.id)
    clock[0] += 180
    await playout_engine.tick(db_session, station)
    assert await _alerts(db_session, station.id) == []


@pytest.mark.asyncio
async def test_no_backend_dead_air_recovery_on_engine_station(db_session, eng, monkeypatch):
    """B-JOB-46 / B-MODE-15: nothing 'playing' in the database on an engine station does not
    start the queue from the backend (the engine has its own emergency lane)."""
    from app.services.scheduler_engine import SchedulerEngine

    station, entries, _, _ = eng
    called = []

    async def fake_silence(self, db, st, has_playing_asset, is_blacked_out):
        called.append(st.id)

    async def fake_start(db, station_id):
        called.append(("start", station_id))

    monkeypatch.setattr(SchedulerEngine, "_check_silence_detection", fake_silence)
    monkeypatch.setattr(queue_module, "start_from_queue", fake_start)
    st = await db_session.get(Station, station.id)
    await SchedulerEngine()._check_station(db_session, st)
    assert called == []
    assert await _statuses(db_session, entries) == {"pending"}

    browser = Station(id=uuid.uuid4(), name="Browser FM", timezone="America/New_York")
    db_session.add(browser)
    await db_session.commit()
    await SchedulerEngine()._check_station(db_session, browser)
    assert called == [browser.id]


# ── Live shows on an engine station (B-JOB-37, B-MODE-19) ────────────────────


async def _live_show(db, station):
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live",
                    status=LiveShowStatus.SCHEDULED, scheduled_start=now,
                    scheduled_end=now + timedelta(hours=1))
    db.add(show)
    await db.commit()
    return show


@pytest.mark.asyncio
async def test_tick_leaves_the_engine_alone_during_a_live_show(db_session, eng):
    """B-JOB-37: during a show the tick hands nothing over (timed items wait)."""
    station, _, engine, _ = eng
    await _add(db_session, station, title="Time", duration=8.0, kind="time", position=50,
               timing_mode="hard", preempt_at=datetime.now(UTC) + timedelta(seconds=20))
    st = await db_session.get(Station, station.id)
    st.automation_config = {"live_show_id": str(uuid.uuid4())}
    await db_session.commit()
    await playout_engine.tick(db_session, st)
    assert engine.sent == []


@pytest.mark.asyncio
async def test_engine_gets_no_music_during_a_live_show(client, db_session, eng):
    """B-MODE-19. Reproduction: song on air on the engine, live show starts
    (_pause_automation queues 'resume@N' first); the engine's prefetch asks /next and is
    handed the resume entry, so it airs during the show and is gone afterwards."""
    from app.services import live_show_service

    station, entries, _, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    row = await _fresh(db_session, entries[0].id)
    row.started_at = datetime.now(UTC) - timedelta(seconds=60)
    await db_session.commit()
    show = await _live_show(db_session, station)
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    r = await _next(client, station, str(entries[0].id))
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_show_end_on_engine_station_leaves_start_to_the_engine(client, db_session, eng):
    """Reproduction: start then end a live show on the engine station with nothing on air;
    afterwards Song 0 is 'playing' in the database without any start report."""
    from app.services import live_show_service

    station, entries, _, _ = eng
    show = await _live_show(db_session, station)
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    await live_show_service.end_show(db_session, show.id)
    await db_session.commit()
    playing = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert playing == []


# ── Long items (shiur) and the timed lane (B-JOB-87, B-MODE-13) ──────────────


async def _shiur_on_air(client, db, station, policy, minutes_in=10):
    st = await db.get(Station, station.id)
    st.automation_config = {"timed_during_long_items": policy}
    shiur = await _add(db, station, title="Shiur", duration=3600.0, kind="shiur", position=0)
    await _next(client, station)
    await _on_air(client, station, shiur.id)
    row = await _fresh(db, shiur.id)
    row.started_at = datetime.now(UTC) - timedelta(minutes=minutes_in)
    await db.commit()
    clock = await _add(db, station, title="Time", duration=8.0, kind="time", position=50,
                       timing_mode="hard", preempt_at=datetime.now(UTC) + timedelta(seconds=30))
    return st, shiur, clock


@pytest.mark.asyncio
async def test_cut_policy_time_check_ends_the_shiur(client, db_session, eng):
    """B-JOB-87 (cut): time check handed to the timed lane; when it starts the shiur is closed."""
    station, _, engine, _ = eng
    st, shiur, clock = await _shiur_on_air(client, db_session, station, "cut")
    await playout_engine.tick(db_session, st)
    assert [c["id"] for c in engine.timed()] == [str(clock.id)]
    await _on_air(client, station, clock.id, lane="timed")
    assert (await _fresh(db_session, shiur.id)).status == "played"
    assert (await _fresh(db_session, clock.id)).status == "playing"


@pytest.mark.asyncio
async def test_pause_policy_resumes_the_shiur_on_the_engine(client, db_session, eng):
    """Reproduction: shiur 10 min in on the engine; hh:00 time check handed and started on the
    timed lane; no 'resume@' entry is queued, so the remaining 50 minutes are lost."""
    station, _, _, _ = eng
    st, shiur, clock = await _shiur_on_air(client, db_session, station, "pause")
    await playout_engine.tick(db_session, st)
    await _on_air(client, station, clock.id, lane="timed")
    pending = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .execution_options(populate_existing=True)
    )).scalars().all()
    resumed = [e for e in pending if e.asset_id == shiur.asset_id and resume_offset(e) > 500]
    assert resumed


@pytest.mark.asyncio
async def test_pause_drops_the_preloaded_song_even_after_a_restart(client, db_session, eng):
    """V1-11 (Jerusalem 2026-11-17 14:00Z): the backend restarted a minute before the hour,
    so it no longer knew which song the engine had pre-loaded and never swapped it for the
    resume. The song aired and the shiur came back 6 minutes late. Making the resume entry
    must always make the engine drop its pre-loaded track."""
    station, _, engine, _ = eng
    st, shiur, _ = await _shiur_on_air(client, db_session, station, "pause")
    state = playout_engine.get_state(station.id)
    state.cued_entry_id = None  # what a restart leaves
    state.last_auto_flush = 0.0
    await playout_engine.tick(db_session, st)
    pending = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert [e for e in pending if e.asset_id == shiur.asset_id and resume_offset(e) > 500]
    assert "/flush" in engine.paths()


@pytest.mark.asyncio
async def test_wait_policy_holds_the_time_check_until_the_shiur_ends(client, db_session, eng):
    """Reproduction: policy wait, shiur 10 of 60 min in; the tick 30 s before hh:00 sends /timed."""
    station, _, engine, _ = eng
    st, _, _ = await _shiur_on_air(client, db_session, station, "wait")
    await playout_engine.tick(db_session, st)
    assert engine.timed() == []


# ── Restart (B-JOB-95/96/97, B-MODE-16) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_restart_leaves_engine_entries_to_the_engine(db_session, eng, monkeypatch):
    """B-JOB-95/96 / B-MODE-16: on restart, a browser station's finished entry is closed,
    logged and the next one started; the engine station's entries are left as they are
    (the engine reports what aired) and nothing is started locally for it."""
    import app.db.engine as db_engine
    from app.main import _resume_playback_on_startup
    from tests.conftest import TestSessionLocal

    station, entries, _, _ = eng
    long_ago = datetime.now(UTC) - timedelta(minutes=10)
    row = await _fresh(db_session, entries[0].id)
    row.status, row.started_at = "playing", long_ago  # 180 s song, "finished" by the clock

    browser = Station(id=uuid.uuid4(), name="Browser FM", timezone="America/New_York")
    db_session.add(browser)
    await db_session.commit()
    b_done = await _add(db_session, browser, title="B old", duration=180.0, position=1,
                        status="playing", started_at=long_ago)
    b_next = await _add(db_session, browser, title="B next", duration=180.0, position=2)

    async def no_replenish(self):
        return None

    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(
        "app.services.queue_replenish_service.QueueReplenishService.replenish", no_replenish)
    await _resume_playback_on_startup()

    assert (await _fresh(db_session, entries[0].id)).status == "playing"
    assert await _statuses(db_session, entries[1:]) == {"pending"}
    logs = (await db_session.execute(select(PlayLog).execution_options(populate_existing=True))).scalars().all()
    assert {log.asset_id for log in logs} == {b_done.asset_id}
    assert (await _fresh(db_session, b_done.id)).status == "played"
    assert (await _fresh(db_session, b_next.id)).status == "playing"
    titles = [a.title for a in await _alerts(db_session)]
    assert "Server restarted" in titles


@pytest.mark.asyncio
async def test_restart_with_empty_queue_alerts_browser_station_only(db_session, eng, monkeypatch):
    """B-JOB-97: a browser station with nothing to play after restart gets a warning; the
    engine station is not started locally (its own 204 -> emergency path reports instead)."""
    import app.db.engine as db_engine
    from app.main import _resume_playback_on_startup
    from tests.conftest import TestSessionLocal

    station, entries, _, _ = eng
    for e in entries:
        (await _fresh(db_session, e.id)).status = "played"
    browser = Station(id=uuid.uuid4(), name="Empty FM", timezone="America/New_York")
    db_session.add(browser)
    await db_session.commit()

    async def no_replenish(self):
        return None

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(
        "app.services.queue_replenish_service.QueueReplenishService.replenish", no_replenish)
    monkeypatch.setattr(queue_module, "_start_next_locked", nothing)
    await _resume_playback_on_startup()
    alerts = await _alerts(db_session)
    assert [a.title for a in alerts if a.station_id == browser.id] == ["Queue empty after restart: Empty FM"]
    assert [a for a in alerts if a.station_id == station.id] == []


# ── Operator actions on an engine station (B-MODE-3/5/7/8) ───────────────────


@pytest.mark.asyncio
async def test_next_keeps_a_timed_item_on_air(client, db_session, auth_headers, eng):
    """B-MODE-3: Next while the time check is on air on the engine: nothing is sent."""
    station, _, engine, _ = eng
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time", position=0,
                       timing_mode="hard", preempt_at=datetime.now(UTC) + timedelta(seconds=20))
    await playout_engine.tick(db_session, station)
    await _on_air(client, station, clock.id, lane="timed")
    engine.sent.clear()
    for fade in ("hard", "fade", "soft"):
        t0 = time.perf_counter()
        r = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade={fade}", headers=auth_headers)
        assert time.perf_counter() - t0 < limit(FAST_SECONDS)
        assert r.status_code == 200 and r.json()["protected"] is True
    assert engine.sent == []


@pytest.mark.asyncio
async def test_skip_fades_sent_to_the_engine(client, db_session, auth_headers, eng):
    """B-MODE-2: hard / fade / soft reach the engine as 0 / 0.5 / 3 s fades, quickly."""
    station, entries, engine, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    for fade, seconds in (("hard", 0.0), ("fade", 0.5), ("soft", 3.0)):
        t0 = time.perf_counter()
        r = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade={fade}", headers=auth_headers)
        assert time.perf_counter() - t0 < limit(FAST_SECONDS)
        assert r.json()["fade_seconds"] == seconds
        assert engine.sent[-1] == ("/skip", {"fade": seconds})


@pytest.mark.asyncio
async def test_mix_over_uses_the_overlay_lane(client, db_session, auth_headers, eng):
    """B-MODE-5: mix-over -> /overlay with the asset; the queue does not move; engine down -> 503."""
    station, entries, engine, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    jingle = Asset(id=uuid.uuid4(), title="Jingle", duration=5.0, asset_type="jingle",
                   file_path="https://cdn.example.com/audio/jingle.mp3",
                   metadata_extra={"audio_analysis": {"replay_gain_db": -2.0}})
    no_file = Asset(id=uuid.uuid4(), title="Nothing", duration=5.0, asset_type="jingle", file_path="")
    db_session.add_all([jingle, no_file])
    await db_session.commit()
    engine.sent.clear()

    t0 = time.perf_counter()
    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(jingle.id)})
    assert time.perf_counter() - t0 < limit(FAST_SECONDS)
    assert r.status_code == 200 and r.json()["on_air"] is True
    assert [p for p, _ in engine.sent] == ["/overlay"]
    uri = engine.sent[0][1]["uri"]
    assert 'liq_amplify="-2.00 dB"' in uri
    assert 'kbr_lane="overlay"' in uri and uri.endswith(":https://cdn.example.com/audio/jingle.mp3")
    assert (await _fresh(db_session, entries[0].id)).status == "playing"
    assert (await _fresh(db_session, entries[1].id)).status == "pending"

    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(no_file.id)})
    assert r.status_code == 409
    engine.ok = False
    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(jingle.id)})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_insert_flushes_the_preloaded_track(client, db_session, auth_headers, eng):
    """B-MODE-7: an insert at the top makes the engine drop its pre-loaded track and ask again."""
    station, entries, engine, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    preloaded = (await _next(client, station, str(entries[0].id))).json()["entry_id"]
    assert preloaded == str(entries[1].id)
    extra = Asset(id=uuid.uuid4(), title="Request", duration=200.0, asset_type="music",
                  file_path="https://cdn.example.com/audio/req.mp3")
    db_session.add(extra)
    await db_session.commit()
    engine.sent.clear()
    t0 = time.perf_counter()
    r = await client.post(f"/api/v1/stations/{station.id}/queue/insert", headers=auth_headers,
                          json={"asset_id": str(extra.id)})
    assert time.perf_counter() - t0 < limit(FAST_SECONDS)
    assert r.status_code == 201
    assert ("/flush", None) in engine.sent
    # radio.liq /flush forgets the pre-loaded entry from `given`
    nxt = (await _next(client, station, str(entries[0].id))).json()
    assert nxt["entry_id"] == r.json()["ids"][0]


@pytest.mark.asyncio
async def test_start_leaves_starting_to_the_engine(client, db_session, auth_headers, eng):
    """B-MODE-8: Start on an engine station only makes sure there is something to pull."""
    station, entries, engine, calls = eng
    r = await client.post(f"/api/v1/stations/{station.id}/queue/start", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"message": "Playout engine will start the next track", "now_playing": None}
    assert calls["replenish"] == 1
    assert await _statuses(db_session, entries) == {"pending"}
    assert engine.sent == []


@pytest.mark.asyncio
async def test_a_hung_engine_does_not_stall_the_scheduler_pass(db_session, eng):
    """Reproduction (scaled 1/10): each /timed takes 0.4 s instead of the 4 s timeout; five
    hard block items due in 30 s -> the tick takes 2 s. The scheduler loop (every 3 s) runs
    stations in turn, so browser stations wait as well."""
    station, _, engine, _ = eng
    await playout_engine.handle_hello(station.id, 2)  # current radio.liq: proto 2
    at = datetime.now(UTC) + timedelta(seconds=30)
    for i in range(5):
        await _add(db_session, station, title=f"Block {i}", duration=20.0, kind="spot",
                   position=40 + i, source="block", timing_mode="hard", preempt_at=at)
    engine.delay = 0.4
    t0 = time.perf_counter()
    await playout_engine.tick(db_session, station)
    assert len(engine.timed()) == 5
    assert time.perf_counter() - t0 < limit(0.5)
    # Each carries its place in the group, so the engine can put the group back in order
    assert sorted((c["seq"], c["of"]) for c in engine.timed()) == [(i, 5) for i in range(5)]


@pytest.mark.asyncio
async def test_old_engine_gets_the_group_one_at_a_time(client, db_session, eng):
    """An engine on the old radio.liq (no `proto` in /hello) queues timed items in the
    order they arrive, so a group goes to it one item after another, in play order, with
    no seq/of. KBR Jerusalem ran such an engine when the parallel hand-over was added."""
    station, _, engine, _ = eng
    r = await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS)  # old: no body
    assert r.status_code == 200
    assert playout_engine.get_state(station.id).engine_proto == 0
    at = datetime.now(UTC) + timedelta(seconds=30)
    block = [
        await _add(db_session, station, title=f"Block {i}", duration=20.0, kind="spot",
                   position=40 + i, source="block", timing_mode="hard", preempt_at=at)
        for i in range(3)
    ]
    in_flight = {"now": 0, "max": 0}
    real = engine.command

    async def one_at_a_time(path, payload=None, **kw):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        try:
            await asyncio.sleep(0.05)
            return await real(path, payload, **kw)
        finally:
            in_flight["now"] -= 1

    playout_engine._command = one_at_a_time
    await playout_engine.tick(db_session, station)
    timed = engine.timed()
    assert [c["id"] for c in timed] == [str(e.id) for e in block]
    assert in_flight["max"] == 1
    assert all("seq" not in c and "of" not in c for c in timed)


@pytest.mark.asyncio
async def test_engine_proto_comes_from_hello_and_next(client, db_session, eng):
    """/hello sets the engine's protocol (a hello without it is an old engine again);
    /next carries it too, so a restarted backend learns it within one item."""
    station, _, _, _ = eng
    state = lambda: playout_engine.get_state(station.id)  # noqa: E731
    assert state().engine_proto == 0
    await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS, json={"proto": 2})
    assert state().engine_proto == 2
    await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS, json={})
    assert state().engine_proto == 0
    playout_engine.reset_state()  # backend restart
    await _next(client, station)
    assert state().engine_proto == 0
    await client.get(f"/api/v1/playout/{station.id}/next?given=&proto=2", headers=HEADERS)
    assert state().engine_proto == 2


# ── Verifier round 1, section B (V1-29..40) ──────────────────────────────────
# V1-29 is test_watchdog_alerts_when_the_engine_never_called above.


@pytest.mark.asyncio
async def test_engine_restart_before_the_hour_rehands_the_time_check(client, db_session, eng):
    """V1-30: the engine restarts (hello) at :59:50 after it was handed the hh:00 time check;
    the next tick hands it again, same id and same second."""
    station, _, engine, _ = eng
    at = datetime.now(UTC) + timedelta(seconds=10)
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time",
                       position=50, timing_mode="hard", preempt_at=at)
    await playout_engine.tick(db_session, station)
    r = await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS)
    assert r.status_code == 200
    assert str(clock.id) not in {str(i) for i in playout_engine.held_entry_ids(station.id)}
    await playout_engine.tick(db_session, station)
    timed = engine.timed()
    assert [c["id"] for c in timed] == [str(clock.id)] * 2
    assert timed[0]["at"] == timed[1]["at"] == at.timestamp()


@pytest.mark.asyncio
async def test_unknown_entry_start_closes_the_previous_one(client, db_session, eng):
    """V1-32 (current behaviour): a start report for an entry we never had closes what was
    playing and answers known: false."""
    station, entries, _, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    r = await _on_air(client, station, uuid.uuid4())
    assert r == {"ok": True, "known": False}
    assert (await _fresh(db_session, entries[0].id)).status == "played"


@pytest.mark.asyncio
async def test_deleted_entry_still_on_air_after_backend_restart(client, db_session, eng):
    """V1-32. Reproduction: engine pre-loads Song 0; backend restarts (memory gone); a
    schedule redraw deletes Song 0's entry; the engine starts it and reports it -> no
    entry is 'playing' (test_a_redraw_never_loses_what_the_engine_plays covers only the
    no-restart case)."""
    station, entries, _, _ = eng
    first = (await _next(client, station)).json()
    playout_engine.reset_state()
    held = await _fresh(db_session, entries[0].id)
    await db_session.delete(held)
    await db_session.commit()
    await _on_air(client, station, first["entry_id"])
    playing = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert len(playing) == 1


@pytest.mark.asyncio
async def test_emergency_alert_rearms_after_a_known_start(client, db_session, eng, monkeypatch):
    """V1-33: emergency -> alert; a known entry clears the flag; a later emergency alerts again."""
    station, entries, _, _ = eng
    raised = []

    async def record(db, **kw):
        raised.append(kw["title"])

    import app.services.alert_service as alert_service
    monkeypatch.setattr(alert_service, "create_alert", record)
    await _on_air(client, station, "", lane="emergency")
    await _on_air(client, station, "", lane="emergency")
    assert raised == ["Playout engine is on its emergency playlist"]  # once per spell
    await _on_air(client, station, entries[0].id)
    assert playout_engine.get_state(station.id).on_emergency is False
    await _on_air(client, station, "", lane="emergency")
    assert len(raised) == 2


@pytest.mark.asyncio
async def test_hard_by_fade_waits_until_the_engine_holds_the_group(client, db_session, eng):
    """V1-34: past hard_by, the song is faded only once the engine has the group's first
    item pre-loaded: first a flush (it holds another track), then the skip after it asks."""
    station, entries, engine, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    await _next(client, station, str(entries[0].id))  # engine pre-loads Song 1
    now = datetime.now(UTC)
    spot = await _add(db_session, station, title="Spot", duration=30.0, kind="spot", position=9,
                      source="block", timing_mode="soft", preempt_at=now - timedelta(minutes=3),
                      hard_by=now - timedelta(seconds=1))
    engine.sent.clear()
    await playout_engine.tick(db_session, station)
    assert engine.paths() == ["/flush"]  # no fade yet: the engine would play Song 1 next
    # The engine never re-asks (busy, or the flush was lost): the song is not faded
    await playout_engine.tick(db_session, station)
    assert "/skip" not in engine.paths()
    nxt = (await _next(client, station, str(entries[0].id))).json()
    assert nxt["entry_id"] == str(spot.id)
    engine.sent.clear()
    await playout_engine.tick(db_session, station)
    assert engine.paths() == ["/skip"]


@pytest.mark.asyncio
async def test_next_drops_at_most_five_unplayable_entries(client, db_session, eng, monkeypatch):
    """V1-35: five entries without audio are skipped, then 204; the 6th waits for the next ask."""
    station, entries, _, _ = eng
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    for e in entries:
        (await _fresh(db_session, e.id)).status = "played"
    await db_session.commit()
    bad = []
    for i in range(6):
        bad.append(await _add(db_session, station, title=f"Bad {i}", duration=100.0, position=i + 1))
        asset = await db_session.get(Asset, bad[-1].asset_id)
        asset.file_path = f"relative/{i}.mp3"
    good = await _add(db_session, station, title="Good", duration=100.0, position=20)
    await db_session.commit()
    r = await _next(client, station)
    assert r.status_code == 204
    assert await _statuses(db_session, bad[:5]) == {"skipped"}
    assert (await _fresh(db_session, bad[5].id)).status == "pending"
    r = await _next(client, station)
    assert r.json()["entry_id"] == str(good.id)


@pytest.mark.asyncio
async def test_no_playable_audio_raises_an_alert(client, db_session, eng, monkeypatch):
    """V1-36. Reproduction: SUPABASE_URL unset, every queued item has a relative file_path;
    the engine asks /next -> every entry 'skipped', 204, alerts table empty."""
    station, entries, _, _ = eng
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    for e in entries:
        asset = await db_session.get(Asset, e.asset_id)
        asset.file_path = f"relative/{e.id}.mp3"
    await db_session.commit()
    r = await _next(client, station)
    assert r.status_code == 204
    assert await _alerts(db_session, station.id)


@pytest.mark.asyncio
async def test_engine_needs_url_and_token(client, eng, monkeypatch):
    """V1-37: enabled without URL or token -> no station is engine-driven and /playout refuses."""
    station, _, _, _ = eng
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "")
    assert not settings.playout_engine_enabled and not settings.engine_drives(station.id)
    assert (await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)).status_code == 404
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "")
    assert not settings.engine_drives(station.id)
    # No token configured: every engine call is refused, even one sending an empty header
    for headers in (HEADERS, {"X-Playout-Token": ""}, {}):
        r = await client.get(f"/api/v1/playout/{station.id}/next", headers=headers)
        assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_station_taken_off_the_engine_goes_back_to_the_browser(client, db_session, eng, monkeypatch):
    """V1-38: a station no longer listed: its old engine gets 404 from /next (it falls to its
    emergency lane), and the scheduler advances the station by duration again."""
    from app.services.scheduler_engine import SchedulerEngine

    station, _, _, _ = eng
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(uuid.uuid4()))
    assert (await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)).status_code == 404
    assert (await client.post(f"/api/v1/playout/{station.id}/started", headers=HEADERS,
                              json={"entry_id": None})).status_code == 404
    ticked, advanced = [], []

    async def fake_tick(db, st):
        ticked.append(st.id)

    async def fake_advance(self, db, station_id):
        advanced.append(station_id)

    async def fake_check(self, db, st):
        return None

    monkeypatch.setattr(playout_engine, "tick", fake_tick)
    monkeypatch.setattr(SchedulerEngine, "_advance_queue", fake_advance)
    monkeypatch.setattr(SchedulerEngine, "_check_station", fake_check)
    await SchedulerEngine()._check_all_stations(db_session)
    assert ticked == [] and advanced == [station.id]


@pytest.mark.asyncio
async def test_public_stream_url_rules(eng, monkeypatch):
    """V1-39: legacy Liquidsoap wins (one URL for all); no ICECAST_STREAM_URL -> no stream."""
    station, _, _, _ = eng
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", True)
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "")
    assert settings.public_stream_url(station.id) is None
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "https://stream.example.com/live")
    assert settings.public_stream_url(station.id) == f"https://stream.example.com/live-{station.id}"
    monkeypatch.setattr(settings, "LIQUIDSOAP_ENABLED", True)
    monkeypatch.setattr(settings, "LIQUIDSOAP_HOST", "legacy.example.com")
    assert settings.public_stream_url(station.id) == "https://stream.example.com/live"
    assert settings.public_stream_url(uuid.uuid4()) == "https://stream.example.com/live"


@pytest.mark.asyncio
async def test_mix_over_while_a_timed_item_is_on_air(client, db_session, auth_headers, eng):
    """V1-40: mix-over during the time check still goes to the overlay lane (plays on top)."""
    station, _, engine, _ = eng
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time", position=0,
                       timing_mode="hard", preempt_at=datetime.now(UTC) + timedelta(seconds=20))
    await playout_engine.tick(db_session, station)
    await _on_air(client, station, clock.id, lane="timed")
    jingle = Asset(id=uuid.uuid4(), title="Jingle", duration=5.0, asset_type="jingle",
                   file_path="https://cdn.example.com/audio/j.mp3")
    db_session.add(jingle)
    await db_session.commit()
    engine.sent.clear()
    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(jingle.id)})
    assert r.status_code == 200
    assert engine.paths() == ["/overlay"]
    assert (await _fresh(db_session, clock.id)).status == "playing"
