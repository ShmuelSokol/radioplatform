"""Station.automation_config settings each change what the station does.
B-SET-2, 8, 10, 11, 12, 13, 18, 21 (the others have their own test files)."""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.song_request import RequestStatus, SongRequest
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService


async def _station(db, **config) -> Station:
    st = Station(id=uuid.uuid4(), name=f"S{uuid.uuid4().hex[:4]}", timezone="UTC", is_active=True,
                 automation_config=config)
    db.add(st)
    await db.commit()
    return st


async def _song(db, title, **kw) -> Asset:
    a = Asset(id=uuid.uuid4(), title=title, file_path=f"{title}.mp3", duration=kw.pop("duration", 200),
              asset_type="music", **kw)
    db.add(a)
    await db.commit()
    return a


async def _queued(db, station) -> list:
    rows = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id).order_by(QueueEntry.position)
    )).scalars().all()
    return [r.asset_id for r in rows]


# ---- B-SET-2: hourly_time_announcement ----

@pytest.mark.asyncio
@pytest.mark.parametrize("on", [False, True])
async def test_hourly_time_check_only_when_switched_on(db_session: AsyncSession, monkeypatch, on):
    calls = []

    async def fake_hourly(self):
        calls.append(self.station_id)

    monkeypatch.setattr(QueueReplenishService, "_schedule_hourly_announcements", fake_hourly)
    st = await _station(db_session, hourly_time_announcement=on, weather_enabled=False)
    await _song(db_session, "Filler")
    await QueueReplenishService(db_session, st.id).replenish()
    assert bool(calls) is on


# ---- B-SET-8: ad_slot_minutes ----

@pytest.mark.asyncio
@pytest.mark.parametrize("config,allowed", [({}, {15, 30, 45}), ({"ad_slot_minutes": [20, 40]}, {20, 40})])
async def test_spot_slots_follow_ad_slot_minutes(db_session: AsyncSession, config, allowed):
    from app.models.spot_schedule import SpotSchedule
    from app.models.sponsor import Sponsor
    from app.services.spot_schedule_service import plan_for_station

    st = await _station(db_session, **config)
    spot = Asset(id=uuid.uuid4(), title="Spot", file_path="p.mp3", duration=30, asset_type="spot")
    sp = Sponsor(id=uuid.uuid4(), name="Shop", length_seconds=30, audio_file_path="p.mp3")
    db_session.add_all([spot, sp])
    await db_session.flush()
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sp.id, asset_id=spot.id, station_id=st.id,
                                dayparts="morning,afternoon,evening", plays_per_daypart=3))
    await db_session.commit()
    placements, _ = await plan_for_station(db_session, st, days=2,
                                           now=datetime(2030, 1, 7, 0, 0, tzinfo=UTC))
    assert placements
    assert {p.local.minute for p in placements} <= allowed


# ---- B-SET-10 / B-SET-11: requests_only and popular_request_threshold ----

@pytest.mark.asyncio
async def test_requests_only_station_plays_requests_not_the_library(db_session: AsyncSession):
    st = await _station(db_session, requests_only=True)
    asked = await _song(db_session, "Asked for")
    await _song(db_session, "Never asked")
    db_session.add(SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="A", song_title="Asked for",
                               asset_id=asked.id, status=RequestStatus.APPROVED))
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    await db_session.commit()
    assert await _queued(db_session, st) == [asked.id]
    req = (await db_session.execute(select(SongRequest))).scalar_one()
    assert req.status == RequestStatus.QUEUED


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold,expected", [(2, True), (3, False)])
async def test_popular_request_threshold(db_session: AsyncSession, threshold, expected):
    st = await _station(db_session, requests_only=True, popular_request_threshold=threshold)
    hit = await _song(db_session, "Popular")
    for n in range(2):  # asked twice, already played (not approved/queued any more)
        db_session.add(SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name=f"R{n}", song_title="Popular",
                                   asset_id=hit.id, status=RequestStatus.PLAYED))
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    await db_session.commit()
    assert (hit.id in await _queued(db_session, st)) is expected


# ---- B-SET-12: oldies_only + oldies_min_years ----

@pytest.mark.asyncio
@pytest.mark.parametrize("config,expected", [
    ({}, {"Old", "New", "Undated"}),
    ({"oldies_only": True}, {"Old"}),
    ({"oldies_only": True, "oldies_min_years": 0}, {"Old", "New"}),
])
async def test_oldies_only_filters_by_release_date(db_session: AsyncSession, config, expected):
    st = await _station(db_session, **config)
    today = date.today()
    await _song(db_session, "Old", release_date=today - timedelta(days=20 * 365))
    await _song(db_session, "New", release_date=today - timedelta(days=30))
    await _song(db_session, "Undated")

    class Rule:
        name = "music"
        asset_type = "music"
        category = None

    svc = QueueReplenishService(db_session, st.id)
    await svc._load_automation_config()
    svc.exclude_ids = set()
    found = await svc._find_assets_for_rule(Rule())
    assert {a.title for a in found} == expected


# ---- B-SET-13: silence_threshold_seconds ----

@pytest.mark.asyncio
@pytest.mark.parametrize("config,alerted", [({"silence_threshold_seconds": 5}, True),
                                            ({"silence_threshold_seconds": 120}, False),
                                            ({}, True)])  # default SILENCE_DETECTION_SECONDS (30)
async def test_dead_air_alert_follows_silence_threshold(db_session: AsyncSession, monkeypatch, config, alerted):
    from app.api.v1 import queue as queue_api
    from app.models.alert import Alert
    from app.services.scheduler_engine import SchedulerEngine

    async def no_start(db, station_id):
        return None

    monkeypatch.setattr(queue_api, "start_from_queue", no_start)
    st = await _station(db_session, **config)
    engine = SchedulerEngine()
    engine._silence_start[str(st.id)] = datetime.now(UTC) - timedelta(seconds=40)
    await engine._check_silence_detection(db_session, st, has_playing_asset=False, is_blacked_out=False)
    alerts = (await db_session.execute(select(Alert))).scalars().all()
    assert bool(alerts) is alerted


@pytest.mark.asyncio
async def test_no_dead_air_alert_during_a_blackout(db_session: AsyncSession):
    from app.models.alert import Alert
    from app.services.scheduler_engine import SchedulerEngine

    st = await _station(db_session, silence_threshold_seconds=1)
    engine = SchedulerEngine()
    engine._silence_start[str(st.id)] = datetime.now(UTC) - timedelta(seconds=40)
    await engine._check_silence_detection(db_session, st, has_playing_asset=False, is_blacked_out=True)
    assert (await db_session.execute(select(Alert))).scalars().all() == []


# ---- B-SET-21: is_active ----

@pytest.mark.asyncio
async def test_inactive_station_is_not_advanced(db_session: AsyncSession, monkeypatch):
    from app.services.scheduler_engine import SchedulerEngine

    on = await _station(db_session)
    off = await _station(db_session)
    off.is_active = False
    await db_session.commit()
    visited = []
    engine = SchedulerEngine()

    async def advance(db, station_id):
        visited.append(station_id)

    async def check(db, station):
        return None

    monkeypatch.setattr(engine, "_advance_queue", advance)
    monkeypatch.setattr(engine, "_check_station", check)
    await engine._check_all_stations(db_session)
    assert visited == [on.id]


# ---- B-SET-18: hourly_station_id ----

def test_hourly_station_id_is_read_by_the_hourly_pass():
    """The "Hourly Station ID" switch is honoured by the queue's hourly pass (the old
    template-only path on the UTC clock, which nothing called, is gone). Behaviour is
    tested in tests/test_replenish_guide_forecast.py (V1-79)."""
    import inspect

    from app.services import queue_replenish_service
    from app.services.scheduling import SchedulingService

    assert "hourly_station_id" in inspect.getsource(queue_replenish_service)
    assert not hasattr(SchedulingService, "_check_automation_insertions")
