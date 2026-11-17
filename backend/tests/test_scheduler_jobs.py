"""The scheduler loop and the browser-player advance: duplicate rows, items with no
start time, late or restarted hourly time checks (timed on the real clock with the
scheduler's own timers), blackouts and their silence, dead air, compaction, one
station's failure, and the per-pass session."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import app.api.v1.queue as queue_module
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import scheduler_engine
from app.services.scheduler_engine import SchedulerEngine
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    refills = []
    monkeypatch.setattr(queue_module, "_replenish_soon",
                        lambda station_id, rebroadcast=False: refills.append(str(station_id)))
    return refills


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _station(db, *, songs=3, elapsed=60.0, duration=200.0, name="Jobs FM", **kw):
    station = Station(id=uuid.uuid4(), name=name, timezone="America/New_York", **kw)
    db.add(station)
    assets = []
    for i in range(songs):
        a = Asset(id=uuid.uuid4(), title=f"{name} song {i}", duration=duration,
                  file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3", asset_type="music")
        db.add(a)
        assets.append(a)
    await db.flush()
    entries = []
    for i, a in enumerate(assets):
        playing = i == 0 and elapsed is not None
        entries.append(QueueEntry(
            id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=i + 1,
            status="playing" if playing else "pending", source="auto",
            started_at=datetime.now(UTC) - timedelta(seconds=elapsed) if playing else None,
        ))
    db.add_all(entries)
    await db.commit()
    return station, assets, entries


async def _clip(db, title="Time check", duration=6.0, kind="jingle", category="time_announcement"):
    clip = Asset(id=uuid.uuid4(), title=title, duration=duration, asset_type=kind, category=category,
                 file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(clip)
    await db.flush()
    return clip


async def _rows(db, station_id, *statuses):
    q = select(QueueEntry).where(QueueEntry.station_id == station_id)
    if statuses:
        q = q.where(QueueEntry.status.in_(statuses))
    return (await db.execute(q.order_by(QueueEntry.position)
                             .execution_options(populate_existing=True))).scalars().all()


# ── Advance basics ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_playing_rows_are_cleaned_to_the_newest(db_session):
    """B-JOB-9"""
    station, assets, entries = await _station(db_session, elapsed=30.0)
    entries[1].status = "playing"
    entries[1].started_at = datetime.now(UTC) - timedelta(seconds=90)
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.id == entries[0].id
    playing = await _rows(db_session, station.id, "playing")
    assert [p.id for p in playing] == [entries[0].id]
    assert (await db_session.get(QueueEntry, entries[1].id)).status == "played"


@pytest.mark.asyncio
async def test_playing_entry_without_start_time_gets_now(db_session):
    """B-JOB-10"""
    station, assets, entries = await _station(db_session, elapsed=30.0)
    entries[0].started_at = None
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.id == entries[0].id
    assert abs((datetime.now(UTC) - _aware(on_air.started_at)).total_seconds()) < 2


@pytest.mark.asyncio
async def test_item_without_length_is_timed_as_three_minutes(db_session):
    """V1-54"""
    station, assets, entries = await _station(db_session, elapsed=170.0)
    assets[0].duration = None
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).id == entries[0].id
    entries[0].started_at = datetime.now(UTC) - timedelta(seconds=181)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).id == entries[1].id


@pytest.mark.asyncio
async def test_positions_compacted_and_refill_after_the_change(db_session, _quiet):
    """B-JOB-24: after an automatic change the item on air is position 1 and the rest
    follow without gaps; the top-up runs afterwards (in the background)"""
    station, assets, entries = await _station(db_session, songs=4, elapsed=201.0)
    entries[2].position = 17
    entries[3].position = 40
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.id == entries[1].id
    rows = await _rows(db_session, station.id, "pending", "playing")
    assert [(r.id, r.position) for r in rows] == [
        (entries[1].id, 1), (entries[2].id, 2), (entries[3].id, 3)]
    assert _quiet == [str(station.id)]
    log = (await db_session.execute(select(PlayLog).where(
        PlayLog.station_id == station.id))).scalars().all()
    assert [lg.asset_id for lg in log] == [assets[0].id]


@pytest.mark.asyncio
async def test_timed_item_of_the_same_file_as_on_air_does_not_cut_it(db_session):
    """V1-53: a timed copy of the item on air waits (no restart of the same file)"""
    station, assets, entries = await _station(db_session, elapsed=30.0)
    twin = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id, position=9,
                      status="pending", preempt_at=datetime.now(UTC) - timedelta(seconds=1),
                      timing_mode="hard")
    db_session.add(twin)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).id == entries[0].id
    assert (await db_session.get(QueueEntry, twin.id)).status == "pending"


# ── Hourly time check: late, restarted, on the real clock ─────────────────


@pytest.mark.asyncio
async def test_time_check_queued_late_airs_at_once_not_by_position(db_session):
    """B-JOB-20: the hourly entry was created after its time (preempt_at already past)
    and sits at the end of the queue: it still cuts the song at once"""
    station, assets, entries = await _station(db_session, songs=6, elapsed=60.0)
    clip = await _clip(db_session)
    late = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=99,
                      status="pending", preempt_at=datetime.now(UTC) - timedelta(seconds=40),
                      timing_mode="hard")
    db_session.add(late)
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.id == late.id
    assert (await db_session.get(QueueEntry, entries[0].id)).status == "played"
    # After it, the queue carries on with the next song, not the one that was cut
    on_air.started_at = datetime.now(UTC) - timedelta(seconds=10)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).id == entries[1].id


async def _engine_on_test_db(monkeypatch):
    engine = SchedulerEngine()
    engine._leader_until = datetime.now(UTC) + timedelta(minutes=5)
    monkeypatch.setattr(scheduler_engine, "async_session_factory", TestSessionLocal)
    sent = []

    async def record(db, station_id, entry, change_fade_seconds=None):
        sent.append((entry.id, datetime.now(UTC)))

    monkeypatch.setattr(engine, "_broadcast_queue_entry", record)
    return engine, sent


@pytest.mark.asyncio
async def test_time_check_starts_on_the_second_with_the_real_timers(db_session, monkeypatch):
    """B-JOB-17 (in process, real asyncio timers and clock): a song crosses the hour;
    the scheduler's poll arms the exact timer and the time check starts within 0.5 s of
    its time, not up to a poll interval (3 s) late."""
    engine, sent = await _engine_on_test_db(monkeypatch)
    station, assets, entries = await _station(db_session, songs=3, elapsed=60.0)
    clip = await _clip(db_session)
    due = datetime.now(UTC) + timedelta(seconds=1.5)
    check = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=50,
                       status="pending", preempt_at=due, timing_mode="hard")
    db_session.add(check)
    await db_session.commit()

    async with TestSessionLocal() as db:
        await engine._advance_queue(db, station.id)  # one ordinary 3 s poll
        await db.rollback()
    assert str(station.id) in engine._timed_timers
    await asyncio.sleep(2.2)
    try:
        row = await db_session.get(QueueEntry, check.id, populate_existing=True)
        assert row.status == "playing"
        late = (_aware(row.started_at) - due).total_seconds()
        assert -0.05 <= late < 0.5, late
        assert sent and sent[-1][0] == check.id
        assert (sent[-1][1] - due).total_seconds() < 0.5
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_time_check_after_a_restart_just_before_the_hour(db_session, monkeypatch):
    """B-JOB-19: the backend restarted at :59:50 and its first pass comes a few seconds
    after the hour (or the pass was slow): the time check goes on air on that first pass,
    the song is cut and logged, and the next song follows the time check."""
    station, assets, entries = await _station(db_session, songs=3, elapsed=100.0)
    clip = await _clip(db_session)
    check = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=50,
                       status="pending", preempt_at=datetime.now(UTC) - timedelta(seconds=4),
                       timing_mode="hard")
    db_session.add(check)
    await db_session.commit()
    engine, sent = await _engine_on_test_db(monkeypatch)  # a fresh process: no timers
    try:
        async with TestSessionLocal() as db:
            await engine._advance_queue(db, station.id)
            await db.rollback()
        row = await db_session.get(QueueEntry, check.id, populate_existing=True)
        assert row.status == "playing"
        assert sent[-1][0] == check.id
        logs = (await db_session.execute(select(PlayLog).where(
            PlayLog.station_id == station.id))).scalars().all()
        assert [lg.asset_id for lg in logs] == [assets[0].id]
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_precise_advance_skipped_without_leadership(db_session, monkeypatch):
    """B-JOB-21 / V1-56: a timer armed before the process lost the lease does nothing"""
    station, assets, entries = await _station(db_session, elapsed=199.0)
    engine, sent = await _engine_on_test_db(monkeypatch)
    engine._leader_until = None
    await engine._precise_advance(station.id, 3.0)
    assert (await db_session.get(QueueEntry, entries[0].id, populate_existing=True)).status == "playing"
    assert sent == []
    engine._leader_until = datetime.now(UTC) + timedelta(seconds=15)
    await engine._precise_advance(station.id, 3.0)
    assert (await db_session.get(QueueEntry, entries[1].id, populate_existing=True)).status == "playing"
    await engine.stop()


# ── Blackouts ─────────────────────────────────────────────────────────────


async def _silence(db, duration=300.0):
    silence = Asset(id=uuid.uuid4(), title="Silence", duration=duration, asset_type="silence",
                    file_path="https://cdn.example.com/silence.mp3")
    db.add(silence)
    await db.flush()
    return silence


@pytest.mark.asyncio
async def test_blackout_ended_while_silence_plays(db_session):
    """B-JOB-22: silence stops at once, its leftover entries are cleared, the next song starts"""
    station, assets, entries = await _station(db_session, songs=2, elapsed=None)
    silence = await _silence(db_session)
    now = datetime.now(UTC)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id, position=0,
                   status="playing", source="auto", started_at=now - timedelta(seconds=20)),
        *[QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id,
                     position=-5 + i, status="pending", source="auto") for i in range(3)],
    ])
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.id == entries[0].id
    left = await _rows(db_session, station.id, "pending", "playing")
    assert silence.id not in {r.asset_id for r in left}


@pytest.mark.asyncio
async def test_blackout_covers_listed_stations_only(db_session):
    """B-JOB-40, V1-89"""
    a, *_ = await _station(db_session, name="A")
    b, *_ = await _station(db_session, name="B")
    engine = SchedulerEngine()
    now = datetime.now(UTC)

    def window(**kw):
        return HolidayWindow(id=uuid.uuid4(), name="W", start_datetime=now - timedelta(hours=1),
                             end_datetime=now + timedelta(hours=1), **kw)

    db_session.add(window(is_blackout=True, affected_stations={"station_ids": [str(a.id)]}))
    db_session.add(window(is_blackout=False, affected_stations=None))
    await db_session.commit()
    assert await engine._is_station_blacked_out(db_session, a, now) is True
    assert await engine._is_station_blacked_out(db_session, b, now) is False
    assert await queue_module._is_blacked_out(db_session, a.id) is True
    assert await queue_module._is_blacked_out(db_session, b.id) is False

    db_session.add(window(is_blackout=True, affected_stations=None))  # all stations
    await db_session.commit()
    assert await engine._is_station_blacked_out(db_session, b, now) is True
    # Moving a blackout's end into the past ends it at once
    rows = (await db_session.execute(select(HolidayWindow))).scalars().all()
    for w in rows:
        w.end_datetime = now - timedelta(seconds=1)
    await db_session.commit()
    assert await engine._is_station_blacked_out(db_session, a, now) is False


@pytest.mark.asyncio
async def test_silence_queued_for_upcoming_and_active_blackouts_once(db_session):
    """B-JOB-41"""
    station, assets, entries = await _station(db_session, songs=3, elapsed=10.0)
    silence = await _silence(db_session)
    now = datetime.now(UTC)
    start = now + timedelta(hours=2)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=start + timedelta(hours=1), is_blackout=True,
                                 affected_stations={"station_ids": [str(station.id)]}))
    await db_session.commit()
    assert await queue_module.fill_blackout_queue(db_session, station.id) == 12
    assert await queue_module.fill_blackout_queue(db_session, station.id) == 0
    rows = [r for r in await _rows(db_session, station.id, "pending") if r.asset_id == silence.id]
    assert len(rows) == 12
    assert all(_aware(r.preempt_at) == start for r in rows)
    # The song on air is not touched by an upcoming blackout
    assert (await db_session.get(QueueEntry, entries[0].id)).status == "playing"


@pytest.mark.asyncio
async def test_active_blackout_silences_the_station_now(db_session):
    """B-JOB-41: an active blackout stops the song and starts silence"""
    station, assets, entries = await _station(db_session, songs=3, elapsed=10.0)
    silence = await _silence(db_session)
    now = datetime.now(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Yom Tov", start_datetime=now - timedelta(minutes=5),
                                 end_datetime=now + timedelta(minutes=14), is_blackout=True))
    await db_session.commit()
    assert await queue_module.fill_blackout_queue(db_session, station.id) == 3
    playing = await _rows(db_session, station.id, "playing")
    assert [p.asset_id for p in playing] == [silence.id]
    assert await queue_module.fill_blackout_queue(db_session, station.id) == 0


@pytest.mark.asyncio
async def test_no_time_check_on_air_during_shabbos(db_session):
    """V1-89/B-JOB-55: during a blackout only silence plays, even when a time check queued
    before Shabbos comes due (it used to cut the silence and air on Shabbos morning)"""
    station, assets, entries = await _station(db_session, songs=2, elapsed=None)
    silence = await _silence(db_session)
    clip = await _clip(db_session)
    now = datetime.now(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=now - timedelta(hours=3),
                                 end_datetime=now + timedelta(hours=20), is_blackout=True))
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id, position=0,
                   status="playing", source="auto", started_at=now - timedelta(seconds=20)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id, position=1,
                   status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=30,
                   status="pending", preempt_at=now - timedelta(seconds=1), timing_mode="hard"),
    ])
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.asset_id == silence.id
    # When the silence on air ends, the next silence follows, not the time check
    on_air.started_at = now - timedelta(seconds=301)
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.asset_id == silence.id


@pytest.mark.asyncio
async def test_hourly_time_checks_are_not_queued_inside_a_blackout(db_session, monkeypatch):
    """B-JOB-55 / V1-89: the hourly pass leaves the hours of Shabbos empty"""
    from app.config import settings
    from app.services import weather_spot_service
    from app.services.queue_replenish_service import QueueReplenishService

    station, assets, entries = await _station(db_session, songs=2, elapsed=10.0)
    clip = await _clip(db_session)
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))

    async def clips(db, slot_key, region=None):
        return clip, None

    monkeypatch.setattr(weather_spot_service, "get_or_create_weather_spot_assets", clips)
    now = datetime.now(UTC)
    start, end = now + timedelta(hours=5), now + timedelta(hours=30)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=end, is_blackout=True))
    await db_session.commit()
    service = QueueReplenishService(db_session, station.id)
    await service._load_automation_config()
    service.max_pos = 10
    await service._schedule_hourly_announcements()
    await db_session.commit()
    times = [_aware(r.preempt_at) for r in await _rows(db_session, station.id, "pending")
             if r.preempt_at is not None]
    assert times and not any(start <= t < end for t in times)
    assert any(t >= end for t in times) and any(t < start for t in times)


# ── Dead air ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_air_timer_resets_and_ignores_blackouts(db_session, monkeypatch):
    """B-JOB-45"""
    station, *_ = await _station(db_session, elapsed=None)
    engine = SchedulerEngine()
    started = []

    async def fake_start(db, station_id):
        started.append(station_id)
        return None

    monkeypatch.setattr(queue_module, "start_from_queue", fake_start)
    key = str(station.id)
    station.automation_config = {"silence_threshold_seconds": 30}

    await engine._check_silence_detection(db_session, station, False, False)
    assert key in engine._silence_start
    # Something plays: the spell is over
    await engine._check_silence_detection(db_session, station, True, False)
    assert key not in engine._silence_start and key not in engine._silence_alerted
    # A blackout is not dead air
    engine._silence_start[key] = datetime.now(UTC) - timedelta(minutes=5)
    await engine._check_silence_detection(db_session, station, False, True)
    assert key not in engine._silence_start and started == []
    # Below the threshold: no alert, no restart
    engine._silence_start[key] = datetime.now(UTC) - timedelta(seconds=10)
    await engine._check_silence_detection(db_session, station, False, False)
    assert started == []
    # Past it: one alert per spell, restart tried on each pass
    engine._silence_start[key] = datetime.now(UTC) - timedelta(seconds=31)
    await engine._check_silence_detection(db_session, station, False, False)
    await engine._check_silence_detection(db_session, station, False, False)
    assert len(started) == 2
    alerts = (await db_session.execute(select(func.count(Alert.id)).where(
        Alert.station_id == station.id))).scalar()
    assert alerts == 1


@pytest.mark.asyncio
async def test_inactive_station_is_left_off_air(db_session, monkeypatch):
    """V1-58: the way to keep a browser station off the air is to make it inactive; the
    scheduler then does not restart it"""
    station, *_ = await _station(db_session, elapsed=None, is_active=False)
    engine = SchedulerEngine()
    checked = []

    async def spy(db, st):
        checked.append(st.id)

    monkeypatch.setattr(engine, "_check_station", spy)
    await engine._check_all_stations(db_session)
    assert station.id not in checked


# ── One station failing, session per pass ────────────────────────────────


@pytest.mark.asyncio
async def test_one_station_failing_does_not_stop_the_others(db_session, monkeypatch):
    """B-JOB-4, V1-63: the failing station gets one critical alert (repeats fold into it),
    the other station still advances in the same pass"""
    bad, *_ = await _station(db_session, name="Bad", elapsed=30.0)
    good, g_assets, g_entries = await _station(db_session, name="Good", elapsed=201.0)
    engine = SchedulerEngine()
    real_check = engine._check_station
    bad_id, good_next = bad.id, g_entries[1].id

    async def check(db, station):
        if station.id == bad_id:
            raise RuntimeError("boom")
        await real_check(db, station)

    monkeypatch.setattr(engine, "_check_station", check)
    monkeypatch.setattr(engine, "_broadcast_queue_entry", lambda *a, **k: asyncio.sleep(0))
    for _ in range(3):
        await engine._check_all_stations(db_session)
    assert (await db_session.get(QueueEntry, good_next, populate_existing=True)).status == "playing"
    alerts = (await db_session.execute(select(Alert).where(Alert.station_id == bad_id))).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].title == "Station check failed: Bad"
    assert str(alerts[0].severity.value if hasattr(alerts[0].severity, "value") else alerts[0].severity) == "critical"
    for t in engine._advance_timers.values():
        t.cancel()


@pytest.mark.asyncio
async def test_each_pass_ends_its_transaction(db_session, monkeypatch):
    """B-JOB-3: the loop opens one session per pass and leaves no transaction open"""
    await _station(db_session, elapsed=30.0)
    sessions = []

    class Recording:
        def __call__(self):
            s = TestSessionLocal()
            sessions.append(s)
            return s

    monkeypatch.setattr(scheduler_engine, "async_session_factory", Recording())
    engine = SchedulerEngine(check_interval=0)
    monkeypatch.setattr(engine, "_broadcast_queue_entry", lambda *a, **k: asyncio.sleep(0))
    for name in ("_maybe_extend_holidays", "_maybe_generate_weather_readouts", "_maybe_queue_weather_readouts"):
        monkeypatch.setattr(engine, name, lambda db: asyncio.sleep(0))
    await engine.start()
    await asyncio.sleep(0.3)
    await engine.stop()
    assert len(sessions) >= 2  # a new one each pass
    assert not any(s.in_transaction() for s in sessions)


# ── Holiday extension (daily) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_holiday_extension_is_per_station(db_session, monkeypatch):
    """B-JOB-47, V1-81: a station with no windows is extended even when another station
    is covered for a year; the check runs once a day"""
    from app.services import station_service

    covered, *_ = await _station(db_session, name="Covered", latitude=40.08, longitude=-74.21)
    bare, *_ = await _station(db_session, name="Bare", latitude=40.08, longitude=-74.21)
    now = datetime.now(UTC)
    for d in range(0, 400, 7):
        db_session.add(HolidayWindow(
            id=uuid.uuid4(), name=f"Shabbos {d}", start_datetime=now + timedelta(days=d),
            end_datetime=now + timedelta(days=d, hours=25), is_blackout=True,
            affected_stations={"station_ids": [str(covered.id)]}))
    await db_session.commit()
    extended = []

    async def fake_generate(db, station, months_ahead=13):
        extended.append(station.name)
        return 0

    monkeypatch.setattr(station_service, "auto_generate_holidays_for_station", fake_generate)
    engine = SchedulerEngine()
    await engine._maybe_extend_holidays(db_session)
    assert extended == ["Bare"]
    await engine._maybe_extend_holidays(db_session)  # same day: not again
    assert extended == ["Bare"]
