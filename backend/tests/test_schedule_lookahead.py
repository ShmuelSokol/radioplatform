import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holiday_window import HolidayWindow
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.scheduling import SchedulingService

NY = ZoneInfo("America/New_York")


async def _station(db: AsyncSession, blocks: list[dict]):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    db.add_all([station, schedule])
    made = []
    for b in blocks:
        block = ScheduleBlock(
            id=uuid.uuid4(),
            schedule_id=schedule.id,
            recurrence_type=b.pop("recurrence_type", RecurrenceType.DAILY),
            priority=b.pop("priority", 0),
            **b,
        )
        db.add(block)
        made.append(block)
    await db.commit()
    return station, made


@pytest.mark.asyncio
async def test_blocks_are_matched_in_station_time(db_session: AsyncSession):
    station, (morning,) = await _station(
        db_session, [{"name": "Morning", "start_time": time(6), "end_time": time(9)}]
    )
    svc = SchedulingService(db_session)
    seven_am_ny = datetime(2026, 11, 16, 7, 0, tzinfo=NY)
    block = await svc.get_active_block_for_station(station.id, seven_am_ny.astimezone(UTC))
    assert block is not None and block.id == morning.id
    # 07:00 UTC is 02:00 in New York: not the morning block
    seven_am_utc = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    assert await svc.get_active_block_for_station(station.id, seven_am_utc) is None


@pytest.mark.asyncio
async def test_look_ahead_reports_and_settles_overlaps(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, (music, shiur, special) = await _station(
        db_session,
        [
            {"name": "Music", "start_time": time(0), "end_time": time(23, 59)},
            {"name": "Shiur", "start_time": time(12), "end_time": time(13)},
            {"name": "Special", "start_time": time(20), "end_time": time(21), "priority": 5},
        ],
    )
    url = f"/api/v1/schedules/look-ahead?station_id={station.id}&days=2"
    r = await client.get(url, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    overlaps = [f for f in body["findings"] if f["kind"] == "overlap"]

    # Higher priority: settled by priority, nothing to do
    # The first one may already be under way (clipped to now); a whole day's occurrence
    # is always in the two days looked at
    special_f = next(
        f for f in overlaps
        if '"Special"' in f["title"].split(" overlaps ")[0] and f["local_start"].endswith("20:00")
    )
    assert special_f["needs_attention"] is False
    assert [b["name"] for b in special_f["blocks"] if b["wins"]] == ["Special"]
    assert special_f["local_start"].endswith("20:00") and special_f["local_end"].endswith("21:00")

    # Equal priority: the shorter block wins by rule, and it is flagged for a look
    shiur_f = next(f for f in overlaps if f["title"].startswith('"Shiur"'))
    assert shiur_f["needs_attention"] is True
    assert body["needs_attention"] >= 2 and body["auto_resolved"] >= 2

    # The engine makes the same choice
    noon = datetime.now(NY).replace(hour=12, minute=30) + timedelta(days=1)
    block = await SchedulingService(db_session).get_active_block_for_station(station.id, noon)
    assert block.id == shiur.id

    # A manager settles it: Music should win at noon
    r = await client.post(
        "/api/v1/schedules/look-ahead/settle",
        json={"winner_block_id": str(music.id), "other_block_ids": [str(shiur.id)]},
        headers=auth_headers,
    )
    assert r.status_code == 200 and r.json()["priority"] == 1
    db_session.expire_all()
    body = (await client.get(url, headers=auth_headers)).json()
    noon_f = [f for f in body["findings"] if f["local_start"].endswith("12:00")]
    assert noon_f and all(f["needs_attention"] is False for f in noon_f)
    assert all(b["name"] == "Music" for f in noon_f for b in f["blocks"] if b["wins"])


@pytest.mark.asyncio
async def test_look_ahead_gaps_and_off_air(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, _ = await _station(
        db_session, [{"name": "Day", "start_time": time(6), "end_time": time(22)}]
    )
    # Start the off-air window at 2:07 AM station time tomorrow: inside the
    # overnight gap and off the 15-minute grid, whatever time the test runs.
    tomorrow = (datetime.now(NY) + timedelta(days=1)).date()
    off_start = datetime.combine(tomorrow, time(2, 7), tzinfo=NY).astimezone(UTC)
    db_session.add(
        HolidayWindow(
            id=uuid.uuid4(),
            name="Shabbos",
            start_datetime=off_start,
            end_datetime=off_start + timedelta(hours=25),
        )
    )
    await db_session.commit()
    r = await client.get(
        f"/api/v1/schedules/look-ahead?station_id={station.id}&days=3", headers=auth_headers
    )
    body = r.json()
    kinds = [f["kind"] for f in body["findings"]]
    assert "gap" in kinds and kinds.count("off_air") == 1
    assert body["needs_attention"] == 0
    # no gap is reported inside the off-air window
    off = next(f for f in body["findings"] if f["kind"] == "off_air")
    for f in body["findings"]:
        if f["kind"] == "gap":
            assert f["end"] <= off["start"] or f["start"] >= off["end"]


@pytest.mark.asyncio
async def test_start_only_playlists_hand_over_like_the_studio_program(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """'6 am mix 01234 6:00', 'blanks Candle+1', 'motzei 72': each runs until the next starts."""
    from app.models.schedule_block import SunEvent

    weekdays = ["sunday", "monday", "tuesday", "wednesday", "thursday"]
    station, (mix, night, candle, motzei, shiur) = await _station(
        db_session,
        [
            {
                "name": "6 am mix",
                "start_time": time(6),
                "end_time": time(6),
                "until_next": True,
                "recurrence_type": RecurrenceType.WEEKLY,
                "recurrence_pattern": weekdays,
            },
            {
                "name": "Night",
                "start_time": time(22),
                "end_time": time(22),
                "until_next": True,
                "recurrence_type": RecurrenceType.WEEKLY,
                "recurrence_pattern": weekdays,
            },
            {
                "name": "blanks Candle+1",
                "start_time": time(16),
                "end_time": time(16),
                "until_next": True,
                "recurrence_type": RecurrenceType.WEEKLY,
                "recurrence_pattern": ["friday"],
                "start_sun_event": SunEvent.SUNSET,
                "start_sun_offset": -17,  # candle lighting (18 before sunset) plus 1 minute
            },
            {
                "name": "motzei 72",
                "start_time": time(18),
                "end_time": time(18),
                "until_next": True,
                "recurrence_type": RecurrenceType.WEEKLY,
                "recurrence_pattern": ["saturday"],
                "start_sun_event": SunEvent.SUNSET,
                "start_sun_offset": 72,
            },
            {"name": "Shiur", "start_time": time(12), "end_time": time(13)},
        ],
    )
    station.latitude, station.longitude = 40.0821, -74.2097  # Lakewood
    await db_session.commit()
    svc = SchedulingService(db_session)

    async def on_at(*when):
        block = await svc.get_active_block_for_station(
            station.id, datetime(*when, tzinfo=NY).astimezone(UTC)
        )
        return block.name if block else None

    # Monday 16 Nov 2026
    assert await on_at(2026, 11, 16, 7, 0) == "6 am mix"
    assert await on_at(2026, 11, 16, 12, 30) == "Shiur"  # a timed block sits on top
    assert await on_at(2026, 11, 16, 13, 0) == "6 am mix"  # and the playlist resumes
    assert await on_at(2026, 11, 16, 23, 0) == "Night"
    assert await on_at(2026, 11, 17, 3, 0) == "Night"  # still on after midnight
    # Friday 20 Nov: Thursday's Night runs through the day (no Friday mix) until candle lighting.
    # Lakewood sunset that day is about 4:36 PM, so candle lighting plus 1 is about 4:19 PM.
    assert await on_at(2026, 11, 20, 16, 10) == "Night"
    assert await on_at(2026, 11, 20, 16, 25) == "blanks Candle+1"
    assert await on_at(2026, 11, 21, 11, 0) == "blanks Candle+1"  # all Shabbos day
    # Shabbos ends 72 minutes after sunset, about 5:47 PM
    assert await on_at(2026, 11, 21, 17, 40) == "blanks Candle+1"
    assert await on_at(2026, 11, 21, 17, 55) == "motzei 72"
    assert await on_at(2026, 11, 22, 5, 0) == "motzei 72"
    assert await on_at(2026, 11, 22, 6, 0) == "6 am mix"  # Sunday morning

    # the look-ahead does not call a timed block over the running playlist a conflict
    r = await client.get(
        "/api/v1/schedules/look-ahead",
        params={"station_id": str(station.id), "days": 7},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert [f for f in r.json()["findings"] if f["kind"] in ("overlap", "gap")] == []

    # and the flag round-trips through the API
    r = await client.get(f"/api/v1/schedules/blocks/{mix.id}", headers=auth_headers)
    assert r.status_code == 200 and r.json()["until_next"] is True
