"""The half hour can be on the second too, with its own time check.

Shmuel, 2026-11-18: "on the half hour, it should be exactly on the half hour, like
the announcement, like it's 10:30 ... so people know to come to hear the weather on
the half hour", and :15/:45 stay after the song; a setting he controls.

- automation_config.spot_timing.half_hour: "hard" or "soft" (unset = same as "other")
- automation_config.half_hour_time_check: play the uploaded :30 clip ("The time is
  10:30 ...") before the :30 weather; uploaded with PUT /time-announcements/{hour}?minute=30
"""
import io
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService
from app.services.spot_schedule_service import default_timing, timing_choices
from app.services.weather_spot_service import find_time_clip_asset
from tests.test_weather_spots import _patches

NY = ZoneInfo("America/New_York")


def _local_hour(at: datetime) -> int:
    return at.astimezone(NY).hour


def test_half_hour_follows_other_until_chosen():
    assert [default_timing(m) for m in (0, 15, 30, 45)] == ["hard", "soft", "soft", "soft"]
    hard_half = {"half_hour": "hard"}
    assert [default_timing(m, hard_half) for m in (0, 15, 30, 45)] == ["hard", "soft", "hard", "soft"]
    # a station that made every other slot hard and never chose the half hour
    assert default_timing(30, {"other": "hard"}) == "hard"
    assert default_timing(30, {"other": "hard", "half_hour": "soft"}) == "soft"
    assert timing_choices(None) == {"on_the_hour": "hard", "half_hour": "soft", "other": "soft"}
    assert timing_choices({"half_hour": "loud"})["half_hour"] == "soft"  # junk ignored


async def _put_clip(client: AsyncClient, headers: dict, hour: int, minute: int | None = None, region=None):
    params = {}
    if minute is not None:
        params["minute"] = minute
    if region:
        params["region"] = region
    return await client.put(
        f"/api/v1/time-announcements/{hour}",
        params=params or None,
        files={"file": ("t.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        data={"script": "The time is 10:30"},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_half_hour_clips_upload_beside_the_hour_clips(client: AsyncClient, auth_headers: dict, db_session):
    p1, p2, p3 = _patches(4.0)
    with p1, p2, p3:
        top = await _put_clip(client, auth_headers, 10)
        half = await _put_clip(client, auth_headers, 10, 30)
        half_again = await _put_clip(client, auth_headers, 10, 30)
        jm_half = await _put_clip(client, auth_headers, 10, 30, "jerusalem")
        bad = await _put_clip(client, auth_headers, 10, 15)
    assert top.status_code == 200 and half.status_code == 200, (top.text, half.text)
    assert bad.status_code == 422
    assert top.json()["minute"] == 0 and half.json()["minute"] == 30
    assert top.json()["asset_id"] != half.json()["asset_id"]
    assert half_again.json()["asset_id"] == half.json()["asset_id"] and half_again.json()["replaced"]
    assert "time/10_30_" in half.json()["file_url"] and "time/jerusalem_10_30_" in jm_half.json()["file_url"]

    listed = (await client.get("/api/v1/time-announcements", headers=auth_headers)).json()
    assert [(c["hour"], c["minute"]) for c in listed] == [(10, 0), (10, 30)]
    titles = sorted(a.title for a in (await db_session.execute(select(Asset))).scalars())
    assert titles == ["Time Announcement - 10:00", "Time Announcement - 10:30",
                      "Time Announcement - 10:30 (Jerusalem)"]

    # the top of the hour still plays the :00 clip, never the :30 one
    assert str((await find_time_clip_asset(db_session, 10)).id) == top.json()["asset_id"]
    assert str((await find_time_clip_asset(db_session, 10, minute=30)).id) == half.json()["asset_id"]
    assert str((await find_time_clip_asset(db_session, 10, "jerusalem", 30)).id) == jm_half.json()["asset_id"]
    assert await find_time_clip_asset(db_session, 11, minute=30) is None


async def _setup(db: AsyncSession, config: dict, half_clips: bool = True):
    base = {"hourly_time_announcement": True, "weather_enabled": True, "weather_interval_minutes": 30,
            "blocks_keep_hourly": True}
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True,
                      automation_config={**base, **config})
    clock = Asset(id=uuid.uuid4(), title="Time Announcement - 15:00", duration=5.0, file_path="t.mp3",
                  asset_type="jingle", category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather Report", duration=40.0, file_path="w.mp3",
                    asset_type="spot", category="weather_spot")
    sponsor = Asset(id=uuid.uuid4(), title="TEST SPONSOR", duration=30.0, file_path="s.mp3",
                    asset_type="spot", category="sponsor")
    db.add_all([station, clock, weather, sponsor])
    halves = {}
    if half_clips:
        for h in range(24):
            a = Asset(id=uuid.uuid4(), title=f"Time Announcement - {h:02d}:30", duration=5.0,
                      file_path=f"t{h}30.mp3", asset_type="jingle", category="time_announcement",
                      metadata_extra={"time_of_day": f"{h:02d}", "time_minute": "30"})
            halves[h] = a
            db.add(a)
    await db.flush()
    return station, clock, weather, sponsor, halves


async def _hourly(db: AsyncSession, station, clock, weather, max_pos=100):
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        svc = QueueReplenishService(db, station.id)
        svc.max_pos = max_pos
        await svc._load_automation_config()
        await svc._schedule_hourly_announcements()
        await db.commit()


async def _groups(db: AsyncSession, station) -> dict:
    rows = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )).scalars().all()
    out: dict = {}
    for e in rows:
        at = e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC)
        out.setdefault(at, []).append(e)
    return out


@pytest.mark.asyncio
async def test_half_hour_plays_time_then_weather_then_the_sponsors_on_the_second(db_session, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, sponsor, halves = await _setup(
        db_session, {"half_hour_time_check": True, "spot_timing": {"half_hour": "hard"}})
    # sponsorships of the :30 block were queued first (lower positions)
    half = (datetime.now(UTC) + timedelta(hours=3)).replace(minute=30, second=0, microsecond=0)
    for pos in (1, 2):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=sponsor.id, position=pos,
                                  status="pending", source="block", preempt_at=half, timing_mode="soft"))
    await db_session.commit()

    await _hourly(db_session, station, clock, weather)
    groups = await _groups(db_session, station)

    group = groups[half]
    assert [e.asset_id for e in group] == [halves[_local_hour(half)].id, weather.id, sponsor.id, sponsor.id]
    assert [e.timing_mode for e in group[:2]] == ["hard", "hard"]
    assert [e.source for e in group[2:]] == ["block", "block"]
    # every half hour ahead: its own clip, then the weather; every top of the hour: time, weather
    for at, entries in groups.items():
        ids = [e.asset_id for e in entries]
        if at.minute == 30:
            assert ids[:2] == [halves[_local_hour(at)].id, weather.id], at
        else:
            assert ids == [clock.id, weather.id], at
            assert {e.timing_mode for e in entries} == {"hard"}

    # a second pass queues nothing twice
    before = len((await db_session.execute(select(QueueEntry))).scalars().all())
    await _hourly(db_session, station, clock, weather, max_pos=1000)
    assert len((await db_session.execute(select(QueueEntry))).scalars().all()) == before


@pytest.mark.asyncio
async def test_no_half_hour_time_check_unless_switched_on(db_session, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _, halves = await _setup(db_session, {})
    await db_session.commit()
    await _hourly(db_session, station, clock, weather)
    for at, entries in (await _groups(db_session, station)).items():
        if at.minute == 30:
            assert [e.asset_id for e in entries] == [weather.id]
            assert entries[0].timing_mode == "soft"  # the half hour follows "other" until chosen


@pytest.mark.asyncio
async def test_switched_on_with_no_clip_recorded_plays_the_weather_alone(db_session, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _, _ = await _setup(db_session, {"half_hour_time_check": True}, half_clips=False)
    await db_session.commit()
    await _hourly(db_session, station, clock, weather)
    halves = [v for at, v in (await _groups(db_session, station)).items() if at.minute == 30]
    assert halves and all([e.asset_id for e in v] == [weather.id] for v in halves)


@pytest.mark.asyncio
async def test_switching_on_later_puts_the_time_before_the_weather_already_queued(db_session, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _, halves = await _setup(db_session, {})
    await db_session.commit()
    await _hourly(db_session, station, clock, weather)

    station.automation_config = {**station.automation_config, "half_hour_time_check": True}
    await db_session.commit()
    await _hourly(db_session, station, clock, weather, max_pos=1000)
    for at, entries in (await _groups(db_session, station)).items():
        if at.minute == 30:
            assert [e.asset_id for e in entries] == [halves[_local_hour(at)].id, weather.id], at


@pytest.mark.asyncio
async def test_a_half_hour_clip_queued_at_the_wrong_minute_is_replaced(db_session):
    station, clock, weather, _, halves = await _setup(db_session, {"half_hour_time_check": True})
    top = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    half = top + timedelta(minutes=30)
    wrong = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=halves[_local_hour(top)].id, position=1,
                       status="pending", source="auto", preempt_at=top)
    right = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=halves[_local_hour(half)].id, position=2,
                       status="pending", source="auto", preempt_at=half)
    db_session.add_all([wrong, right])
    await db_session.commit()
    svc = QueueReplenishService(db_session, station.id)
    await svc._load_automation_config()
    removed = await svc._drop_wrong_hour_announcements(NY)
    await db_session.commit()
    left = {e.id for e in (await db_session.execute(select(QueueEntry))).scalars()}
    assert removed == 1 and left == {right.id}


@pytest.mark.asyncio
async def test_timing_setting_api_and_queued_items_follow_it(client: AsyncClient, auth_headers: dict, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC", is_active=True, automation_config={})
    weather = Asset(id=uuid.uuid4(), title="Weather Report", duration=40.0, file_path="w.mp3",
                    asset_type="spot", category="weather_spot")
    db_session.add_all([station, weather])
    half = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=30, second=0, microsecond=0)
    queued = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=1,
                        status="pending", source="auto", preempt_at=half, timing_mode="soft")
    db_session.add(queued)
    await db_session.commit()

    url = "/api/v1/spot-schedules/timing-defaults"
    sid = str(station.id)
    r = await client.get(url, params={"station_id": sid}, headers=auth_headers)
    assert r.json() == {"on_the_hour": "hard", "half_hour": "soft", "other": "soft", "half_hour_time_check": False}

    r = await client.put(url, json={"station_id": sid, "on_the_hour": "hard", "other": "soft",
                                    "half_hour": "hard", "half_hour_time_check": True}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"on_the_hour": "hard", "half_hour": "hard", "other": "soft", "half_hour_time_check": True}
    mode = (await db_session.execute(
        select(QueueEntry.timing_mode).where(QueueEntry.id == queued.id))).scalar()
    assert mode == "hard"

    # a page that does not know the half hour keeps the station's choice
    r = await client.put(url, json={"station_id": sid, "on_the_hour": "hard", "other": "soft"},
                         headers=auth_headers)
    assert r.json()["half_hour"] == "hard" and r.json()["half_hour_time_check"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("config,queued,alarm", [
    ({"half_hour_time_check": True, "spot_timing": {"half_hour": "hard"}}, True, True),
    ({"half_hour_time_check": True, "spot_timing": {"half_hour": "soft"}}, True, False),  # waits by design
    ({"spot_timing": {"half_hour": "hard"}}, True, False),                             # check off
    ({"half_hour_time_check": True, "spot_timing": {"half_hour": "hard"}}, False, False),  # no clip queued
])
async def test_air_watch_reports_a_missing_half_hour_time_check(db_session, config, queued, alarm):
    from app.services.air_watch import AirWatch
    from tests.test_air_watch import _asset, _entry, _station

    st = await _station(db_session, **config)
    song = await _asset(db_session, "Song")
    tc = await _asset(db_session, "Time 9:30", category="time_announcement", kind="jingle", duration=6)
    half = datetime(2026, 11, 17, 2, 30, tzinfo=UTC)  # 9:30 PM New York
    now = half + timedelta(seconds=50)
    rows = [_entry(st, song, position=2, status="playing", started_at=now - timedelta(seconds=30))]
    if queued:  # queued for 9:30 but skipped: never started
        rows.append(_entry(st, tc, status="skipped", preempt_at=half, timing_mode="hard"))
    db_session.add_all(rows)
    await db_session.commit()
    found = [f for f in await AirWatch().check_station(db_session, st, now) if f.kind == "hour_missing"]
    assert bool(found) == alarm, found
    if alarm:
        assert "9:30 PM" in found[0].title


@pytest.mark.asyncio
async def test_the_hour_already_begun_still_gets_its_half_hour(db_session, monkeypatch):
    """Back from a two-day Yom Tov at 9:01 PM (station simulator, last days of Pesach
    5787): nothing was filled during the blackout and time and weather are queued only
    48 h ahead, so no fill ever saw 9:00 PM as a coming hour and the 9:30 time check
    and weather were missing. The same after a start with an empty queue."""
    import time_machine

    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _, halves = await _setup(
        db_session, {"half_hour_time_check": True, "spot_timing": {"half_hour": "hard"}})
    await db_session.commit()
    back = datetime(2027, 4, 29, 21, 1, 21, tzinfo=NY)
    with time_machine.travel(back, tick=False):
        await _hourly(db_session, station, clock, weather)
        groups = await _groups(db_session, station)
        half = datetime(2027, 4, 29, 21, 30, tzinfo=NY).astimezone(UTC)
        assert half in groups, sorted(groups)[:3]
        assert [e.asset_id for e in groups[half]] == [halves[21].id, weather.id]
        assert [e.timing_mode for e in groups[half]] == ["hard", "hard"]
        # nothing for the 9:00 PM that is already past, and the next hour as before
        assert min(groups) == half
        assert [e.asset_id for e in groups[half + timedelta(minutes=30)]] == [clock.id, weather.id]
        # a second pass adds nothing
        before = len((await db_session.execute(select(QueueEntry))).scalars().all())
        await _hourly(db_session, station, clock, weather, max_pos=1000)
        assert len((await db_session.execute(select(QueueEntry))).scalars().all()) == before


@pytest.mark.asyncio
async def test_a_half_hour_already_past_is_not_queued(db_session, monkeypatch):
    import time_machine

    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _, _ = await _setup(db_session, {"half_hour_time_check": True})
    await db_session.commit()
    with time_machine.travel(datetime(2027, 4, 29, 21, 31, 0, tzinfo=NY), tick=False):
        await _hourly(db_session, station, clock, weather)
        groups = await _groups(db_session, station)
        assert min(groups) == datetime(2027, 4, 29, 22, 0, tzinfo=NY).astimezone(UTC)
