"""Which schedule block is on air: overnight blocks, recurrence, ranking, start-only
blocks made after their start, and sun-relative times (V1-65..70, V2-102, V2-103)."""
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock, SunEvent
from app.models.station import Station
from app.services.scheduling import SchedulingService

NY = ZoneInfo("America/New_York")
LONG_AGO = datetime.now(UTC) - timedelta(days=60)


async def _station(db, **kw):
    station = Station(id=uuid.uuid4(), name=f"Blocks {uuid.uuid4().hex[:4]}",
                      timezone=kw.pop("timezone", "America/New_York"), **kw)
    db.add(station)
    await db.commit()
    return station


async def _blocks(db, station, specs, priority=0):
    sched = Schedule(id=uuid.uuid4(), station_id=station.id, name="S", is_active=True, priority=priority)
    db.add(sched)
    await db.flush()
    for kw in specs:
        kw = dict(kw)
        kw.setdefault("recurrence_type", RecurrenceType.DAILY)
        block = ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, **kw)
        block.created_at = kw.get("created_at", LONG_AGO)
        db.add(block)
    await db.commit()


async def _on(db, station, *when, tz=NY):
    block = await SchedulingService(db).get_active_block_for_station(
        station.id, datetime(*when, tzinfo=tz).astimezone(UTC))
    return block.name if block else None


@pytest.mark.asyncio
async def test_overnight_weekly_block_runs_past_midnight(db_session):
    """V2-102, V1-68: a Friday 22:00-02:00 block is still on at 1 AM Saturday, and is not
    on at 1 AM Friday (that belongs to Thursday night)"""
    station = await _station(db_session)
    await _blocks(db_session, station, [{
        "name": "Friday night", "start_time": time(22), "end_time": time(2),
        "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["friday"],
    }])
    # 2026-11-20 is a Friday
    assert await _on(db_session, station, 2026, 11, 20, 23, 0) == "Friday night"
    assert await _on(db_session, station, 2026, 11, 21, 1, 0) == "Friday night"
    assert await _on(db_session, station, 2026, 11, 21, 2, 30) is None
    assert await _on(db_session, station, 2026, 11, 20, 1, 0) is None


@pytest.mark.asyncio
async def test_overnight_one_time_and_monthly_blocks(db_session):
    """V2-102: the same for a one-time block on a date and a monthly day"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Siyum", "start_time": time(23), "end_time": time(1, 30),
         "recurrence_type": RecurrenceType.ONE_TIME, "start_date": date(2026, 12, 3)},
        {"name": "Rosh Chodesh", "start_time": time(21), "end_time": time(0, 30),
         "recurrence_type": RecurrenceType.MONTHLY, "recurrence_pattern": [10]},
    ])
    assert await _on(db_session, station, 2026, 12, 3, 23, 30) == "Siyum"
    assert await _on(db_session, station, 2026, 12, 4, 1, 0) == "Siyum"
    assert await _on(db_session, station, 2026, 12, 3, 1, 0) is None
    assert await _on(db_session, station, 2026, 12, 11, 0, 15) == "Rosh Chodesh"
    assert await _on(db_session, station, 2026, 12, 10, 0, 15) is None


@pytest.mark.asyncio
async def test_recurrence_types(db_session):
    """V1-69: weekly list / empty list, monthly days, one-time range, one-time with no dates"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Weekdays", "start_time": time(6), "end_time": time(7),
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["monday", "tuesday"]},
        {"name": "Every week day", "start_time": time(8), "end_time": time(9),
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": []},
        {"name": "Range", "start_time": time(10), "end_time": time(11),
         "recurrence_type": RecurrenceType.ONE_TIME,
         "start_date": date(2026, 11, 16), "end_date": date(2026, 11, 18)},
        {"name": "Legacy", "start_time": time(12), "end_time": time(13),
         "recurrence_type": RecurrenceType.ONE_TIME},
    ])
    assert await _on(db_session, station, 2026, 11, 16, 6, 30) == "Weekdays"  # Monday
    assert await _on(db_session, station, 2026, 11, 18, 6, 30) is None  # Wednesday
    assert await _on(db_session, station, 2026, 11, 22, 8, 30) == "Every week day"
    assert await _on(db_session, station, 2026, 11, 18, 10, 30) == "Range"
    assert await _on(db_session, station, 2026, 11, 19, 10, 30) is None
    assert await _on(db_session, station, 2030, 1, 1, 12, 30) == "Legacy"


@pytest.mark.asyncio
async def test_block_ranking(db_session):
    """V1-65: schedule priority, then block priority, then one-time over weekly over daily,
    then the shorter block"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Daily long", "start_time": time(0), "end_time": time(23, 59)},
        {"name": "Daily short", "start_time": time(9), "end_time": time(10)},
        {"name": "Weekly", "start_time": time(8), "end_time": time(12),
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["tuesday"]},
        {"name": "Special", "start_time": time(9, 30), "end_time": time(11),
         "recurrence_type": RecurrenceType.ONE_TIME, "start_date": date(2026, 11, 17)},
        {"name": "Priority", "start_time": time(14), "end_time": time(15), "priority": 5},
        {"name": "Afternoon", "start_time": time(14), "end_time": time(14, 30)},
    ])
    assert await _on(db_session, station, 2026, 11, 16, 9, 15) == "Daily short"  # Monday
    assert await _on(db_session, station, 2026, 11, 17, 9, 15) == "Weekly"  # Tuesday
    assert await _on(db_session, station, 2026, 11, 17, 9, 45) == "Special"
    assert await _on(db_session, station, 2026, 11, 16, 14, 10) == "Priority"
    await _blocks(db_session, station, [{"name": "Override", "start_time": time(0), "end_time": time(23, 59)}],
                  priority=10)
    assert await _on(db_session, station, 2026, 11, 17, 9, 45) == "Override"


@pytest.mark.asyncio
async def test_start_only_block_saved_after_its_time_waits_for_its_next_start(db_session):
    """V1-66, V1-67"""
    station = await _station(db_session)
    made = datetime(2026, 11, 17, 6, 40, tzinfo=NY).astimezone(UTC)
    await _blocks(db_session, station, [
        {"name": "Base", "start_time": time(5), "end_time": time(5), "until_next": True},
        {"name": "New", "start_time": time(6, 30), "end_time": time(6, 30), "until_next": True,
         "created_at": made},
    ])
    assert await _on(db_session, station, 2026, 11, 17, 6, 50) == "Base"
    assert await _on(db_session, station, 2026, 11, 18, 6, 35) == "New"
    assert await _on(db_session, station, 2026, 11, 19, 4, 0) == "New"  # stays on until Base's start


@pytest.mark.asyncio
async def test_sunset_block_uses_the_station_location(db_session):
    """V1-70: a sunset block follows the station's own sunset"""
    station = await _station(db_session, latitude=40.0821, longitude=-74.2097)
    await _blocks(db_session, station, [{
        "name": "After sunset", "start_time": time(17), "end_time": time(23),
        "start_sun_event": SunEvent.SUNSET, "start_sun_offset": 10,
    }])
    # Lakewood sunset on 17 Nov 2026 is about 16:38: on at 16:55, off at 16:40
    assert await _on(db_session, station, 2026, 11, 17, 16, 55) == "After sunset"
    assert await _on(db_session, station, 2026, 11, 17, 16, 40) is None


@pytest.mark.asyncio
async def test_polar_summer_does_not_break_block_resolution(db_session):
    """V2-103"""
    station = await _station(db_session, latitude=69.65, longitude=18.96, timezone="Europe/Oslo")
    await _blocks(db_session, station, [
        {"name": "Day", "start_time": time(0), "end_time": time(23, 59)},
        {"name": "Evening", "start_time": time(20), "end_time": time(23), "priority": 5,
         "start_sun_event": SunEvent.SUNSET},
    ])
    oslo = ZoneInfo("Europe/Oslo")
    assert await _on(db_session, station, 2027, 6, 21, 12, 0, tz=oslo) == "Day"
