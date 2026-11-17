"""Restart and deploy overlap, generated Shabbos / Yom Tov windows, time zones of the
hourly time check, long items (wait / cut / long songs), and what happens when what is
on air is deleted."""
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry, resume_offset
from app.models.station import Station
from app.services import long_items
from app.services.queue_replenish_service import QueueReplenishService, local_hour_marks
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_replenish(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    queue_module._fire_requests.clear()


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _station(db, *, name="Restart FM", elapsed=60.0, duration=200.0, songs=3, kind="music", **kw):
    station = Station(id=uuid.uuid4(), name=name, timezone=kw.pop("timezone", "America/New_York"), **kw)
    db.add(station)
    assets = []
    for i in range(songs):
        a = Asset(id=uuid.uuid4(), title=f"{name} {i}", duration=duration if i == 0 else 180.0,
                  file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3",
                  asset_type=kind if i == 0 else "music")
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


async def _rows(db, station_id, *statuses):
    q = select(QueueEntry).where(QueueEntry.station_id == station_id)
    if statuses:
        q = q.where(QueueEntry.status.in_(statuses))
    return (await db.execute(q.order_by(QueueEntry.position)
                             .execution_options(populate_existing=True))).scalars().all()


# ── Restart / deploy ──────────────────────────────────────────────────────


@pytest.fixture
def restart(monkeypatch):
    import app.db.engine as db_engine
    from app.main import _resume_playback_on_startup

    async def no_replenish(self):
        return None

    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(QueueReplenishService, "replenish", no_replenish)
    return _resume_playback_on_startup


@pytest.mark.asyncio
async def test_restart_settles_finished_items_but_not_engine_stations(db_session, restart, monkeypatch):
    """B-JOB-95, B-JOB-97: a browser station's item that ended during the downtime is
    marked played and logged, and the next one starts; an engine station's entries are
    left for the engine; a "Server restarted" info alert sums it up"""
    from app.config import settings

    browser, b_assets, b_entries = await _station(db_session, name="Browser", elapsed=600.0)
    engine_st, e_assets, e_entries = await _station(db_session, name="Engine", elapsed=600.0)
    engine_id = engine_st.id
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: str(sid) == str(engine_id))
    await restart()

    assert (await db_session.get(QueueEntry, b_entries[0].id, populate_existing=True)).status == "played"
    assert (await db_session.get(QueueEntry, b_entries[1].id, populate_existing=True)).status == "playing"
    assert (await db_session.get(QueueEntry, e_entries[0].id, populate_existing=True)).status == "playing"
    assert (await db_session.get(QueueEntry, e_entries[1].id, populate_existing=True)).status == "pending"
    logs = (await db_session.execute(select(PlayLog.asset_id))).scalars().all()
    assert logs == [b_assets[0].id]
    titles = (await db_session.execute(select(Alert.title))).scalars().all()
    assert "Server restarted" in titles


@pytest.mark.asyncio
async def test_restart_with_an_empty_queue_raises_a_warning(db_session, restart):
    """B-JOB-97: nothing to play after the restart -> "Queue empty after restart" alert"""
    station = Station(id=uuid.uuid4(), name="Empty FM", timezone="UTC")
    db_session.add(station)
    await db_session.commit()
    await restart()
    alert = (await db_session.execute(select(Alert).where(Alert.station_id == station.id))).scalar_one()
    assert alert.title == "Queue empty after restart: Empty FM"
    assert str(getattr(alert.severity, "value", alert.severity)) == "warning"


@pytest.mark.asyncio
async def test_deploy_overlap_does_not_start_a_second_item(db_session, restart):
    """V1-112: the new instance's startup runs while the old one is playing a song:
    the song keeps playing, nothing else starts, nothing is logged twice"""
    station, assets, entries = await _station(db_session, elapsed=30.0)
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id,
                           start_utc=entries[0].started_at, source="scheduler"))
    await db_session.commit()
    await restart()
    playing = await _rows(db_session, station.id, "playing")
    assert [p.id for p in playing] == [entries[0].id]
    logs = (await db_session.execute(select(PlayLog).execution_options(populate_existing=True))).scalars().all()
    assert len(logs) == 1 and logs[0].end_utc is None  # the open log of the song on air stays open


# ── Generated Shabbos and Yom Tov ─────────────────────────────────────────


LAKEWOOD = (40.0821, -74.2097)


async def _silence(db):
    db.add(Asset(id=uuid.uuid4(), title="Silence (5 min)", duration=300.0, asset_type="silence",
                 file_path="system/silence_5min.mp3"))
    await db.commit()


@pytest.mark.asyncio
async def test_two_stations_in_the_same_town_both_get_shabbos(db_session):
    """V1-80: windows were de-duplicated by name and time across stations, so the second
    station in the same area got none and played through Shabbos"""
    from app.services.station_service import auto_generate_holidays_for_station, window_covers_station

    await _silence(db_session)
    one = Station(id=uuid.uuid4(), name="One", timezone="America/New_York",
                  latitude=LAKEWOOD[0], longitude=LAKEWOOD[1])
    two = Station(id=uuid.uuid4(), name="Two", timezone="America/New_York",
                  latitude=LAKEWOOD[0], longitude=LAKEWOOD[1])
    db_session.add_all([one, two])
    await db_session.commit()
    first = await auto_generate_holidays_for_station(db_session, one, months_ahead=2)
    second = await auto_generate_holidays_for_station(db_session, two, months_ahead=2)
    await db_session.commit()
    assert first > 0 and second == first
    # Running it again creates nothing new for either
    assert await auto_generate_holidays_for_station(db_session, two, months_ahead=2) == 0
    windows = (await db_session.execute(select(HolidayWindow))).scalars().all()
    assert sum(window_covers_station(w, two.id) for w in windows) == second


def test_shabbos_window_runs_from_candle_lighting_to_72_minutes_after_sunset():
    """V1-82"""
    from app.services.shabbos_service import generate_shabbos_windows
    from app.services.sun_service import get_sun_times

    friday = date(2026, 11, 20)
    w = generate_shabbos_windows(*LAKEWOOD, "America/New_York", friday, friday + timedelta(days=1), ["x"])[0]
    fri = get_sun_times(*LAKEWOOD, "America/New_York", friday)["sunset"]
    sat = get_sun_times(*LAKEWOOD, "America/New_York", friday + timedelta(days=1))["sunset"]
    assert abs((datetime.fromisoformat(w["start_datetime"]) - (fri - timedelta(minutes=18))).total_seconds()) < 60
    assert abs((datetime.fromisoformat(w["end_datetime"]) - (sat + timedelta(minutes=72))).total_seconds()) < 60
    assert w["affected_stations"] == {"station_ids": ["x"]}


def test_shabbos_next_to_yom_tov_is_one_window():
    """V1-84: Yom Tov running into Shabbos is one blackout with both names, no music between"""
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    start, end = date(2026, 9, 1), date(2027, 10, 30)
    merged = merge_overlapping_windows(
        generate_shabbos_windows(*LAKEWOOD, "America/New_York", start, end, ["x"])
        + generate_yom_tov_windows(*LAKEWOOD, "America/New_York", start, end, ["x"])
    )
    joined = [w for w in merged if " / " in w["name"]]
    assert joined, "some Yom Tov in a year touches Shabbos"
    spans = sorted((datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"]))
                   for w in merged)
    assert all(b[0] > a[1] for a, b in zip(spans, spans[1:]))  # no overlaps left


def test_yom_tov_in_israel_is_one_day():
    """V1-83"""
    from pyluach.dates import HebrewDate

    from app.services.shabbos_service import generate_yom_tov_windows

    jerusalem = (31.7683, 35.2137)
    sivan6 = HebrewDate(5787, 3, 6).to_pydate()
    windows = generate_yom_tov_windows(*jerusalem, "Asia/Jerusalem", sivan6 - timedelta(days=3),
                                       sivan6 + timedelta(days=3), ["j"])
    shavuot = next(w for w in windows if w["name"].startswith("Shavuot"))
    assert datetime.fromisoformat(shavuot["end_datetime"]).date() == sivan6


# ── Time zones of the hourly time check ───────────────────────────────────


def test_hour_marks_follow_the_local_clock():
    """V1-116, V1-115"""
    kolkata = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 17, 3, 10, tzinfo=UTC)
    marks = local_hour_marks(now, kolkata, 4)
    assert all(m.astimezone(kolkata).minute == 0 for m in marks)
    assert all(m.minute == 30 for m in marks)  # local :00 is UTC :30
    assert len(marks) == 4

    kathmandu = ZoneInfo("Asia/Kathmandu")  # UTC+5:45
    assert all(m.astimezone(kathmandu).minute == 0 for m in local_hour_marks(now, kathmandu, 3))

    ny = ZoneInfo("America/New_York")
    # Autumn: 1 AM happens twice -> two marks that both read 1:00
    autumn = local_hour_marks(datetime(2026, 11, 1, 3, 30, tzinfo=UTC), ny, 4)
    assert [m.astimezone(ny).hour for m in autumn] == [0, 1, 1, 2]
    # Spring: there is no 2 AM
    spring = local_hour_marks(datetime(2027, 3, 14, 5, 30, tzinfo=UTC), ny, 3)
    assert [m.astimezone(ny).hour for m in spring] == [1, 3, 4]


@pytest.mark.asyncio
async def test_time_check_in_india_is_queued_at_local_hour_and_hard(db_session, monkeypatch):
    """V1-116: time checks were queued at UTC hh:00 = 5:30 past the hour in India, and
    counted as soft (minute 30)"""
    from app.config import settings
    from app.services import weather_spot_service

    station, *_ = await _station(db_session, name="Mumbai", timezone="Asia/Kolkata")
    clip = Asset(id=uuid.uuid4(), title="Time", duration=6.0, asset_type="jingle",
                 category="time_announcement", file_path="https://cdn.example.com/t.mp3")
    db_session.add(clip)
    await db_session.commit()
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    keys = []

    async def clips(db, slot_key, region=None):
        keys.append(slot_key)
        return clip, None

    monkeypatch.setattr(weather_spot_service, "get_or_create_weather_spot_assets", clips)
    service = QueueReplenishService(db_session, station.id)
    await service._load_automation_config()
    service.max_pos = 10
    await service._schedule_hourly_announcements()
    await db_session.commit()
    rows = [r for r in await _rows(db_session, station.id, "pending") if r.preempt_at is not None]
    assert len(rows) >= 47
    kolkata = ZoneInfo("Asia/Kolkata")
    for r in rows:
        local = _aware(r.preempt_at).astimezone(kolkata)
        assert local.minute == 0 and r.timing_mode == "hard"
    assert keys[0][-2:] == f"{_aware(rows[0].preempt_at).astimezone(kolkata).hour:02d}"


def test_station_left_at_utc_announces_new_york_time():
    """V1-114 (documented: stations from before time zones read New York's clock)"""
    from app.services.weather_spot_service import announcement_zone

    assert announcement_zone("UTC").key == "America/New_York"
    assert announcement_zone(None).key == "America/New_York"
    assert announcement_zone("Not/AZone").key == "America/New_York"
    assert announcement_zone("Asia/Jerusalem").key == "Asia/Jerusalem"


@pytest.mark.asyncio
async def test_wrong_hour_time_checks_are_replaced(db_session):
    """B-JOB-59: a queued time check saying another clock's hour is removed with its weather"""
    station, *_ = await _station(db_session, name="Jerusalem", timezone="Asia/Jerusalem")
    zone = ZoneInfo("Asia/Jerusalem")
    at = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    local_hour = at.astimezone(zone).hour
    wrong = Asset(id=uuid.uuid4(), title="NY time", duration=6.0, asset_type="jingle",
                  category="time_announcement", file_path="https://cdn.example.com/w.mp3",
                  metadata_extra={"time_of_day": (local_hour + 7) % 24})
    right = Asset(id=uuid.uuid4(), title="Local time", duration=6.0, asset_type="jingle",
                  category="time_announcement", file_path="https://cdn.example.com/r.mp3",
                  metadata_extra={"time_of_day": (local_hour + 1) % 24})
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=20.0, asset_type="jingle",
                    category="weather_spot", file_path="https://cdn.example.com/x.mp3")
    db_session.add_all([wrong, right, weather])
    await db_session.flush()
    at2 = at + timedelta(hours=1)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=wrong.id, position=20,
                   status="pending", preempt_at=at, timing_mode="hard"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=21,
                   status="pending", preempt_at=at, timing_mode="hard"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=right.id, position=22,
                   status="pending", preempt_at=at2, timing_mode="hard"),
    ])
    await db_session.commit()
    service = QueueReplenishService(db_session, station.id)
    removed = await service._drop_wrong_hour_announcements(zone)
    await db_session.commit()
    assert removed == 2
    left = {r.asset_id for r in await _rows(db_session, station.id, "pending")}
    assert right.id in left and wrong.id not in left and weather.id not in left


# ── Long items ────────────────────────────────────────────────────────────


async def _talk_with_hour(db, policy, *, kind="shiur", duration=40 * 60, elapsed=14 * 60,
                          due_after_end=False, hard_by=False):
    station, assets, entries = await _station(
        db, name=f"Talk {policy}", elapsed=elapsed, duration=duration, kind=kind,
        automation_config={"timed_during_long_items": policy})
    clip = Asset(id=uuid.uuid4(), title="Time", duration=6.0, asset_type="jingle",
                 file_path="https://cdn.example.com/t.mp3")
    db.add(clip)
    await db.flush()
    now = datetime.now(UTC)
    due = now - timedelta(seconds=1)
    later = now + timedelta(seconds=duration - elapsed + 600)
    rows = [QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=10,
                       status="pending", preempt_at=due, timing_mode="hard")]
    if hard_by:
        rows.append(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=11,
                               status="pending", preempt_at=due - timedelta(minutes=1),
                               timing_mode="soft", hard_by=due))
    if due_after_end:
        rows.append(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=12,
                               status="pending", preempt_at=later, timing_mode="hard"))
    db.add_all(rows)
    await db.commit()
    return station, assets, entries, rows


@pytest.mark.asyncio
async def test_wait_defers_only_items_due_before_the_shiur_ends(db_session):
    """B-JOB-85: wait turns hard items (and soft-with-limit) due before the end into plain
    soft; one due after the shiur ends stays hard"""
    station, assets, entries, rows = await _talk_with_hour(db_session, "wait", due_after_end=True, hard_by=True)
    assert (await queue_module._check_advance(db_session, station.id)).id == entries[0].id
    now_due, limited, after_end = [await db_session.get(QueueEntry, r.id, populate_existing=True) for r in rows]
    assert now_due.timing_mode == "soft" and now_due.hard_by is None
    assert limited.timing_mode == "soft" and limited.hard_by is None
    assert after_end.timing_mode == "hard"


@pytest.mark.asyncio
async def test_cut_ends_the_shiur_for_good(db_session):
    """B-JOB-86: cut logs the shiur and does not queue it again"""
    station, assets, entries, rows = await _talk_with_hour(db_session, "cut")
    assert (await queue_module._check_advance(db_session, station.id)).id == rows[0].id
    pending = await _rows(db_session, station.id, "pending")
    assert assets[0].id not in {r.asset_id for r in pending}
    logs = (await db_session.execute(select(PlayLog).where(PlayLog.station_id == station.id))).scalars().all()
    assert [lg.asset_id for lg in logs] == [assets[0].id]


@pytest.mark.asyncio
async def test_long_song_counts_as_a_long_item(db_session):
    """V1-117 (current rule, to confirm with the owner): any item of 12 minutes or more,
    a long song included, is paused by the time check and resumed after it"""
    assert long_items.is_long_form(type("A", (), {"asset_type": "music", "duration": 13 * 60})())
    assert not long_items.is_long_form(type("A", (), {"asset_type": "music", "duration": 11 * 60})())
    station, assets, entries, rows = await _talk_with_hour(
        db_session, "pause", kind="music", duration=13 * 60, elapsed=5 * 60)
    assert (await queue_module._check_advance(db_session, station.id)).id == rows[0].id
    resumed = [r for r in await _rows(db_session, station.id, "pending") if r.asset_id == assets[0].id]
    assert len(resumed) == 1 and 290 <= resume_offset(resumed[0]) <= 300


# ── Deleting what is on air ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_removing_the_entry_on_air_moves_on(client, auth_headers, db_session):
    """V1-43"""
    station, assets, entries = await _station(db_session, elapsed=30.0)
    res = await client.delete(f"/api/v1/stations/{station.id}/queue/{entries[0].id}", headers=auth_headers)
    assert res.status_code == 204
    playing = await _rows(db_session, station.id, "playing")
    assert [p.asset_id for p in playing] == [assets[1].id]


@pytest.mark.asyncio
async def test_deleting_the_library_item_on_air_keeps_the_station_playing(client, auth_headers, db_session):
    """V1-91"""
    station, assets, entries = await _station(db_session, elapsed=30.0)
    res = await client.delete(f"/api/v1/assets/{assets[0].id}", headers=auth_headers)
    assert res.status_code in (204, 409)
    playing = await _rows(db_session, station.id, "playing")
    assert len(playing) == 1


@pytest.mark.asyncio
async def test_manual_now_playing_row_is_removed_by_the_next_pass(db_session):
    """V1-57 (documented): a now_playing row (what PATCH /now-playing writes) is removed by
    the one-player cleanup on the next pass; the queue stays what listeners hear"""
    from app.models.now_playing import NowPlaying
    from app.services.scheduler_engine import SchedulerEngine

    station, assets, entries = await _station(db_session, elapsed=30.0)
    db_session.add(NowPlaying(id=uuid.uuid4(), station_id=station.id, asset_id=assets[2].id,
                              started_at=datetime.now(UTC)))
    await db_session.commit()
    await SchedulerEngine()._check_station(db_session, station)
    left = (await db_session.execute(select(NowPlaying).where(NowPlaying.station_id == station.id))).all()
    assert left == []
    assert [p.id for p in await _rows(db_session, station.id, "playing")] == [entries[0].id]
