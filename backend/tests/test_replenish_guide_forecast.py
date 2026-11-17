"""Queue replenishment (rules, full queue, block failures, switching to block
playlists, the hourly station ID), the public program guide, and the weather
forecast's day grouping.

Rows: V1-73, V1-74, V1-75, V1-76, V1-77, V1-78, V1-79, V2-104, V2-105, V2-106."""
import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.services.queue_replenish_service as qrs
from app.config import settings
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog, PlaySource
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock, SunEvent
from app.models.schedule_rule import ScheduleRule
from app.models.station import Station
from app.services.queue_replenish_service import TARGET_QUEUE_SECONDS, QueueReplenishService, local_hour_marks

KOLKATA = ZoneInfo("Asia/Kolkata")
JERUSALEM = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")


def _frozen(at: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.replace(tzinfo=None) if tz is None else at.astimezone(tz)
    return Frozen


def _asset(kind="music", title=None, duration=180.0, **kw) -> Asset:
    return Asset(id=uuid.uuid4(), title=title or f"{kind} {uuid.uuid4().hex[:5]}", duration=duration,
                 asset_type=kind, file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3", **kw)


async def _station(db, tz="UTC", config=None, **kw) -> Station:
    st = Station(id=uuid.uuid4(), name=f"S{uuid.uuid4().hex[:6]}", timezone=tz, is_active=True,
                 automation_config=config or {}, **kw)
    db.add(st)
    await db.commit()
    return st


async def _pending(db, station_id) -> list[tuple[QueueEntry, Asset]]:
    rows = await db.execute(
        select(QueueEntry, Asset).join(Asset, Asset.id == QueueEntry.asset_id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _storage_on(monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))


# ---------------------------------------------------------------- V1-73

@pytest.mark.asyncio
async def test_template_block_never_inserts_a_utc_clock_time_clip(db_session: AsyncSession, monkeypatch):
    """V1-73: a playlist-template block in a half-hour zone (India) at UTC hh:00 (local
    hh:30) gets its template's song, not a time clip, station ID or weather: those are
    queued by the hourly pass at the station's own hh:00 (UTC hh:30 for India)."""
    import app.services.scheduling as scheduling
    from app.services.scheduling import SchedulingService

    config = {"hourly_time_announcement": True, "weather_enabled": True, "hourly_station_id": True,
              "weather_interval_minutes": 30}
    st = await _station(db_session, "Asia/Kolkata", config)
    song = _asset(category="Fast")
    clock = _asset("jingle", "Time Announcement", 5.0, category="time_announcement")
    weather = _asset("spot", "Weather", 40.0, category="weather_spot")
    jingle = _asset("jingle", "Kol Bramah 3:00 PM Station ID", 10.0, category="hourly_id")
    db_session.add_all([song, clock, weather, jingle])
    tpl = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=st.id, is_active=True)
    sched = Schedule(id=uuid.uuid4(), station_id=st.id, name="All", is_active=True)
    db_session.add_all([tpl, sched])
    await db_session.flush()
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=tpl.id, position=0, asset_type="music", category="Fast"))
    block = ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Day", start_time=time(0, 0),
                          end_time=time(23, 59), playlist_template_id=tpl.id)
    db_session.add(block)
    await db_session.commit()
    block = (await db_session.execute(
        select(ScheduleBlock).where(ScheduleBlock.id == block.id)
        .options(selectinload(ScheduleBlock.playlist_template).selectinload(PlaylistTemplate.slots))
    )).scalar_one()

    monkeypatch.setattr(scheduling, "datetime", _frozen(datetime(2026, 11, 18, 10, 0, 30, tzinfo=UTC)))
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        picked = await SchedulingService(db_session)._resolve_template_asset(block, st.id)
    assert str(picked) == str(song.id)

    # The hourly pass puts India's time check at local hh:00 = UTC hh:30
    _storage_on(monkeypatch)
    monkeypatch.setattr(qrs, "datetime", _frozen(datetime(2026, 11, 18, 9, 50, tzinfo=UTC)))
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, None))), \
         patch("app.services.weather_spot_service.weather_region_for_station", new=AsyncMock(return_value="lakewood")):
        svc = QueueReplenishService(db_session, st.id)
        svc.max_pos = 0
        await svc._load_automation_config()
        await svc._schedule_hourly_announcements()
        await db_session.commit()
    times = [_aware(e.preempt_at) for e, a in await _pending(db_session, st.id) if a.id == clock.id]
    assert times, "no time check queued"
    assert all(t.minute == 30 and t.astimezone(KOLKATA).minute == 0 for t in times)
    assert len(times) == len(set(times))


# ---------------------------------------------------------------- V1-74

@pytest.mark.asyncio
async def test_station_rules_replace_global_rules(db_session: AsyncSession):
    """V1-74: a station with any rule of its own gets only its own rules (no global
    music); a station with none gets the global rules."""
    own = await _station(db_session)
    other = await _station(db_session)
    db_session.add_all([
        ScheduleRule(id=uuid.uuid4(), station_id=None, name="Global music", rule_type="daypart",
                     asset_type="music", hour_start=0, hour_end=24, priority=10, is_active=True),
        ScheduleRule(id=uuid.uuid4(), station_id=own.id, name="Stories only", rule_type="daypart",
                     asset_type="story", hour_start=0, hour_end=24, priority=1, is_active=True),
        *[_asset("music", duration=86400.0) for _ in range(3)],
        *[_asset("story", duration=86400.0) for _ in range(3)],
    ])
    await db_session.commit()

    assert [r.name for r in await QueueReplenishService(db_session, own.id)._load_rules()] == ["Stories only"]
    assert [r.name for r in await QueueReplenishService(db_session, other.id)._load_rules()] == ["Global music"]

    await QueueReplenishService(db_session, own.id).replenish()
    await QueueReplenishService(db_session, other.id).replenish()
    assert {a.asset_type for _, a in await _pending(db_session, own.id)} == {"story"}
    assert {a.asset_type for _, a in await _pending(db_session, other.id)} == {"music"}


# ---------------------------------------------------------------- V1-75

@pytest.mark.asyncio
async def test_fill_avoids_queued_and_recent_then_recycles(db_session: AsyncSession):
    """V1-75: the first pass skips songs already queued and songs played in the last
    2 h; later passes take the recent ones, then everything, until the week is full."""
    st = await _station(db_session)
    day = 86400.0
    queued, recent, free1, free2 = (_asset(title=t, duration=day) for t in ("Q", "R", "F1", "F2"))
    db_session.add_all([queued, recent, free1, free2])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=queued.id, position=1, status="pending"))
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=recent.id, source=PlaySource.SCHEDULER,
                           start_utc=datetime.now(UTC) - timedelta(hours=1)))
    await db_session.commit()

    await QueueReplenishService(db_session, st.id).replenish()
    titles = [a.title for _, a in await _pending(db_session, st.id)]
    assert titles[0] == "Q"
    added = titles[1:]
    assert set(added[:2]) == {"F1", "F2"}, added  # pass 1: neither queued nor recent
    assert added[2] == "R", added  # pass 2: recent allowed, queued not
    assert len(titles) * day >= TARGET_QUEUE_SECONDS  # pass 3 recycles everything


@pytest.mark.asyncio
async def test_small_library_fills_without_looping(db_session: AsyncSession):
    """V1-75: one 3-minute song cannot fill a week: the fill stops after its passes
    instead of looping, and nothing crashes."""
    st = await _station(db_session)
    db_session.add(_asset(title="Only song"))
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    entries = await _pending(db_session, st.id)
    assert 1 <= len(entries) <= 10
    assert {a.title for _, a in entries} == {"Only song"}


# ---------------------------------------------------------------- V1-76

@pytest.mark.asyncio
async def test_full_queue_still_gets_hourly_and_ad_slots(db_session: AsyncSession, monkeypatch):
    """V1-76: a queue already holding 7 days gets no more songs, but the hourly time
    check and the clock ad slots are still scheduled."""
    _storage_on(monkeypatch)
    st = await _station(db_session, config={"hourly_time_announcement": True, "ad_slot_legacy_spot": True})
    long_song = _asset(title="Eight days", duration=8 * 86400.0)
    clock = _asset("jingle", "Time Announcement", 5.0, category="time_announcement")
    ad = _asset("spot", "KOL BRAMAH TEST SPONSOR", 30.0)
    db_session.add_all([long_song, clock, ad, *[_asset() for _ in range(5)]])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=long_song.id, position=1, status="pending"))
    await db_session.commit()

    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, None))):
        await QueueReplenishService(db_session, st.id).replenish()
    entries = await _pending(db_session, st.id)
    untimed = [a.title for e, a in entries if e.preempt_at is None]
    assert untimed == ["Eight days"], "content was added to a full queue"
    assert any(a.id == clock.id for e, a in entries), "no hourly time check"
    assert any(e.source == "ad_slot" for e, _ in entries), "no ad slots"


# ---------------------------------------------------------------- V1-77

@pytest.mark.asyncio
async def test_block_expansion_failure_falls_back_to_hourly(db_session: AsyncSession, monkeypatch):
    """V1-77: when program blocks cannot be queued (expansion raises), the station still
    gets its built-in hourly time check and weather."""
    _storage_on(monkeypatch)
    st = await _station(db_session, config={"hourly_time_announcement": True, "weather_enabled": True,
                                            "weather_interval_minutes": 60})
    clock = _asset("jingle", "Time Announcement", 5.0, category="time_announcement")
    weather = _asset("spot", "Weather", 40.0, category="weather_spot")
    db_session.add_all([clock, weather, *[_asset() for _ in range(5)]])
    await db_session.flush()
    db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=st.id, name="Weather Sponsors", times="08:00",
                                timing_mode="hard", items=[{"kind": "asset", "asset_id": str(weather.id)}]))
    await db_session.commit()

    with patch("app.services.program_block_service.reconcile_block_entries",
               new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        await QueueReplenishService(db_session, st.id).replenish()
    entries = await _pending(db_session, st.id)
    marks = {_aware(e.preempt_at) for e, a in entries if a.id == clock.id}
    assert marks, "the station lost its time check"
    assert all(t.minute == 0 for t in marks)
    assert any(a.id == weather.id and e.preempt_at is not None for e, a in entries)


# ---------------------------------------------------------------- V1-78

async def _block_playlist_station(db: AsyncSession, old_songs: int):
    station = Station(id=uuid.uuid4(), name=f"S{uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      is_active=True, automation_config={})
    db.add(station)
    await db.flush()
    sched = Schedule(id=uuid.uuid4(), station_id=station.id, name="Weekdays", is_active=True)
    mix = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db.add_all([sched, mix])
    await db.flush()
    db.add(TemplateSlot(id=uuid.uuid4(), template_id=mix.id, position=0, asset_type="music", category="New"))
    db.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="All day", start_time=time(0, 0),
                         end_time=time(23, 59), playlist_template_id=mix.id))
    old = [_asset(title=f"Old {n}", duration=240.0, category="Old") for n in range(5)]
    new = [_asset(title=f"New {n}", duration=240.0, category="New", artist=f"A{n}") for n in range(60)]
    db.add_all(old + new)
    await db.flush()
    for i in range(old_songs):
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=old[i % 5].id, position=i + 1,
                          status="pending", source="auto"))
    await db.commit()
    return station


@pytest.mark.asyncio
@pytest.mark.parametrize("old_songs,replaced", [(300, True), (10, False)])
async def test_switch_to_block_playlists_replaces_a_long_old_fill(
    db_session: AsyncSession, monkeypatch, old_songs, replaced
):
    """V1-78: a station whose blocks now have playlists, holding more than twice the
    generation look-ahead of old automatic fill (300 songs = 20 h > 2 x 6 h), has it
    replaced at once so the block takes over on time; a short old fill (40 min) is kept."""
    monkeypatch.setattr(qrs, "datetime", _frozen(datetime(2026, 11, 16, 7, 0, tzinfo=NY).astimezone(UTC)))
    st = await _block_playlist_station(db_session, old_songs)
    await QueueReplenishService(db_session, st.id).replenish()
    entries = await _pending(db_session, st.id)
    old_left = [a for _, a in entries if a.category == "Old"]
    if replaced:
        assert old_left == []
        assert any(a.category == "New" for _, a in entries)
    else:
        assert len(old_left) == old_songs


# ---------------------------------------------------------------- V1-79

async def _id_station(db, tz="America/New_York", **config):
    st = await _station(db, tz, {"hourly_station_id": True, **config})
    jingles = {h: _asset("jingle", f"Kol Bramah — {12 if h % 12 == 0 else h % 12}:00 {'AM' if h < 12 else 'PM'} Station ID",
                         10.0, category="hourly_id") for h in range(24)}
    db.add_all(jingles.values())
    await db.commit()
    return st, jingles


@pytest.mark.asyncio
async def test_hourly_station_id_airs_every_local_hour_without_a_template(db_session: AsyncSession):
    """V1-79: "Hourly Station ID" on a station with no playlist template queues the
    station ID jingle for that hour at each of the station's own hh:00 (half-hour
    zones too), soft so it follows the time check and weather of the same group."""
    for tz in ("America/New_York", "Asia/Kolkata"):
        st, jingles = await _id_station(db_session, tz)
        zone = ZoneInfo(tz)
        await QueueReplenishService(db_session, st.id).replenish()
        ids = [(e, a) for e, a in await _pending(db_session, st.id) if a.category == "hourly_id"]
        marks = local_hour_marks(datetime.now(UTC), zone, 48)
        assert sorted(_aware(e.preempt_at) for e, _ in ids) == marks
        for e, a in ids:
            assert a.title == jingles[_aware(e.preempt_at).astimezone(zone).hour].title
            assert e.timing_mode == "soft"

        # A second pass adds nothing
        await QueueReplenishService(db_session, st.id).replenish()
        again = [e for e, a in await _pending(db_session, st.id) if a.category == "hourly_id"]
        assert len(again) == len(ids)


@pytest.mark.asyncio
async def test_hourly_station_id_follows_time_and_weather(db_session: AsyncSession, monkeypatch):
    """V1-79: in the hh:00 group the time check stays first (on the second), then the
    weather, then the station ID."""
    _storage_on(monkeypatch)
    st, jingles = await _id_station(db_session, "UTC", hourly_time_announcement=True, weather_enabled=True,
                                    weather_interval_minutes=60)
    clock = _asset("jingle", "Time Announcement", 5.0, category="time_announcement")
    weather = _asset("spot", "Weather", 40.0, category="weather_spot")
    db_session.add_all([clock, weather])
    await db_session.commit()
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        await QueueReplenishService(db_session, st.id).replenish()
    entries = await _pending(db_session, st.id)
    first = local_hour_marks(datetime.now(UTC), UTC, 1)[0]
    group = [a.category for e, a in entries if e.preempt_at is not None and _aware(e.preempt_at) == first]
    assert group == ["time_announcement", "weather_spot", "hourly_id"]


@pytest.mark.asyncio
async def test_hourly_station_id_skips_blackouts_and_needs_a_jingle(db_session: AsyncSession):
    """V1-79: no station ID inside Shabbos / Yom Tov; with no station ID jingle in the
    library nothing is queued (the switch is harmless until one is uploaded)."""
    st, _ = await _id_station(db_session, "UTC")
    now = datetime.now(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=now,
                                 end_datetime=now + timedelta(hours=5), is_blackout=True))
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    marks = [_aware(e.preempt_at) for e, a in await _pending(db_session, st.id) if a.category == "hourly_id"]
    assert marks and min(marks) >= now + timedelta(hours=5)

    bare = await _station(db_session, config={"hourly_station_id": True})
    for j in (await db_session.execute(select(Asset).where(Asset.category == "hourly_id"))).scalars().all():
        await db_session.delete(j)
    await db_session.commit()
    await QueueReplenishService(db_session, bare.id).replenish()
    assert not [a for _, a in await _pending(db_session, bare.id) if a.asset_type == "jingle"]


# ---------------------------------------------------------------- V2-104 / V2-105

async def _guide_station(db) -> tuple[Station, Schedule]:
    st = Station(id=uuid.uuid4(), name=f"KBR Jerusalem {uuid.uuid4().hex[:4]}", timezone="Asia/Jerusalem",
                 is_active=True, latitude=31.7683, longitude=35.2137)
    db.add(st)
    await db.flush()
    sched = Schedule(id=uuid.uuid4(), station_id=st.id, name="Main", is_active=True)
    db.add(sched)
    await db.commit()
    return st, sched


@pytest.mark.asyncio
async def test_guide_default_date_is_the_stations_date(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """V2-104: with no date the guide answers the station's own date: at 23:30 UTC it is
    already tomorrow in Jerusalem."""
    import app.api.v1.schedules as schedules_api

    st, sched = await _guide_station(db_session)
    db_session.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Night", start_time=time(0, 0),
                                 end_time=time(6, 0)))
    await db_session.commit()
    monkeypatch.setattr(schedules_api, "datetime", _frozen(datetime(2026, 11, 17, 23, 30, tzinfo=UTC)))
    r = await client.get(f"/api/v1/schedules/epg/{st.id}")
    assert r.status_code == 200, r.text
    assert r.json()["date"] == "2026-11-18"


def _future_day() -> date:
    return (datetime.now(JERUSALEM) + timedelta(days=14)).date()


@pytest.mark.asyncio
async def test_guide_shows_real_sunset_time(client: AsyncClient, db_session: AsyncSession):
    """V2-105: a block that starts at sunset shows that day's real sunset in Jerusalem,
    not its placeholder clock time."""
    from app.services.sun_service import get_sun_times

    st, sched = await _guide_station(db_session)
    db_session.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Evening", start_time=time(20, 0),
                                 end_time=time(23, 0), start_sun_event=SunEvent.SUNSET, start_sun_offset=0))
    await db_session.commit()
    day = _future_day()
    sunset = get_sun_times(st.latitude, st.longitude, st.timezone, day)["sunset"].strftime("%H:%M")
    r = await client.get(f"/api/v1/schedules/epg/{st.id}", params={"date": day.isoformat()})
    blocks = r.json()["blocks"]
    assert [(b["name"], b["start_time"], b["end_time"]) for b in blocks] == [("Evening", sunset, "23:00")]


@pytest.mark.asyncio
async def test_guide_shows_the_winning_block_and_until_next_ends(client: AsyncClient, db_session: AsyncSession):
    """V2-105: where blocks overlap only the winner is listed for the time it wins;
    a start-only ("until next") block shows the hours it really airs, not its stored
    end time."""
    st, sched = await _guide_station(db_session)
    db_session.add_all([
        ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Morning", start_time=time(6, 0),
                      end_time=time(12, 0), priority=0),
        ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Special", start_time=time(9, 0),
                      end_time=time(10, 0), priority=5),
        ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Low", start_time=time(13, 0),
                      end_time=time(14, 0), priority=-1),
        ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="High", start_time=time(13, 0),
                      end_time=time(14, 0), priority=3),
        ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Evening playlist", start_time=time(20, 0),
                      end_time=time(20, 30), until_next=True),
    ])
    await db_session.commit()
    day = _future_day()
    r = await client.get(f"/api/v1/schedules/epg/{st.id}", params={"date": day.isoformat()})
    got = [(b["name"], b["start_time"], b["end_time"]) for b in r.json()["blocks"]]
    # The start-only playlist (started 20:00 the day before) is the base layer: it
    # airs wherever no timed block does, up to the next start-only block.
    assert got == [
        ("Evening playlist", "00:00", "06:00"),
        ("Morning", "06:00", "09:00"),
        ("Special", "09:00", "10:00"),
        ("Morning", "10:00", "12:00"),
        ("Evening playlist", "12:00", "13:00"),
        ("High", "13:00", "14:00"),
        ("Evening playlist", "14:00", "00:00"),
    ]


@pytest.mark.asyncio
async def test_guide_shows_blackouts_as_off_air(client: AsyncClient, db_session: AsyncSession):
    """V2-105: a Shabbos / Yom Tov blackout shows as off air in place of the blocks."""
    st, sched = await _guide_station(db_session)
    db_session.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="All day", start_time=time(0, 0),
                                 end_time=time(23, 59)))
    day = _future_day()
    start = datetime.combine(day, time(16, 0), tzinfo=JERUSALEM).astimezone(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=start + timedelta(hours=26), is_blackout=True))
    await db_session.commit()
    r = await client.get(f"/api/v1/schedules/epg/{st.id}", params={"date": day.isoformat()})
    got = [(b["name"], b["start_time"], b["end_time"], b["off_air"]) for b in r.json()["blocks"]]
    assert got == [("All day", "00:00", "16:00", False), ("Shabbos", "16:00", "00:00", True)]


# ---------------------------------------------------------------- V2-106

def _item(at: datetime, high: float, low: float, desc: str) -> dict:
    return {"dt": int(at.timestamp()), "main": {"temp_max": high, "temp_min": low}, "weather": [{"description": desc}]}


def test_forecast_days_are_station_days_across_dst():
    """V2-106: forecast days are the station's own dates, today left out, the next three
    days; high = max and low = min of the 3-hour values. At 11 PM on Saturday 31 Oct
    2026 (New York, the night clocks go back) "tomorrow" is Sunday 1 Nov."""
    from app.services.weather_service import daily_forecast

    now = datetime(2026, 10, 31, 23, 0, tzinfo=NY)
    start = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)  # 8 PM Saturday in New York
    items = []
    for k in range(40):
        at = start + timedelta(hours=3 * k)
        local = at.astimezone(NY)
        items.append(_item(at, 50 + local.day + k % 3, 30 + local.day - k % 3, f"sky {local.date()}"))
    out = daily_forecast(list(reversed(items)), "America/New_York", now=now)
    assert [d["day"] for d in out] == ["Sunday", "Monday", "Tuesday"]
    for d, day in zip(out, (1, 2, 3)):
        vals = [i for i in items if datetime.fromtimestamp(i["dt"], UTC).astimezone(NY).date() == date(2026, 11, day)]
        assert d["high"] == round(max(i["main"]["temp_max"] for i in vals))
        assert d["low"] == round(min(i["main"]["temp_min"] for i in vals))
        assert d["description"] == f"sky 2026-11-0{day}"
    # Sunday's values: 1 AM EDT .. 10 PM EST, none from Saturday night
    sunday = [i for i in items if datetime.fromtimestamp(i["dt"], UTC).astimezone(NY).date() == date(2026, 11, 1)]
    assert len(sunday) == 8


@pytest.mark.asyncio
async def test_fetch_weather_uses_the_station_day_grouping(monkeypatch):
    """V2-106: the fetched forecast is grouped by the station's own dates (Jerusalem)."""
    import app.services.weather_service as ws

    now = datetime.now(JERUSALEM)
    tomorrow = (now + timedelta(days=1)).date()
    base = datetime.combine(now.date(), time(0, 0), tzinfo=JERUSALEM)
    forecast = {"list": [_item(base + timedelta(hours=3 * k), 60 + k, 40 - k, "clear") for k in range(40)]}
    current = {"weather": [{"description": "sunny"}], "wind": {"speed": 5, "deg": 90},
               "main": {"temp": 70.2, "humidity": 40}}

    class Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return Resp(forecast if url.endswith("/forecast") else current)

    monkeypatch.setattr(ws.httpx, "AsyncClient", Client)
    out = await ws._fetch_weather(31.77, 35.21, "Asia/Jerusalem")
    assert out["temp_f"] == 70 and out["wind_direction"] == "east"
    assert [d["day"] for d in out["forecast"]][0] == tomorrow.strftime("%A")
    assert len(out["forecast"]) == 3
