"""Block resolution matrix: ranking, start-only blocks, overnight blocks, recurrence,
sun-relative times, playback modes and template rotation (V1-65..72, V2-102, V2-103)."""
import random
import uuid
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.playlist_entry import PlaybackMode
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock, SunEvent
from app.models.station import Station
from app.services.scheduling import SchedulingService

NY = ZoneInfo("America/New_York")
OSLO = ZoneInfo("Europe/Oslo")
JERUSALEM = ZoneInfo("Asia/Jerusalem")
# A fixed date, before every date these tests ask about: "60 days before today" made
# them fail from mid January 2027 on (found by running the suite with the clock moved)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


async def _station(db, **kw):
    station = Station(id=uuid.uuid4(), name=f"Matrix {uuid.uuid4().hex[:4]}",
                      timezone=kw.pop("timezone", "America/New_York"), **kw)
    db.add(station)
    await db.commit()
    return station


async def _blocks(db, station, specs, priority=0):
    sched = Schedule(id=uuid.uuid4(), station_id=station.id, name="S", is_active=True, priority=priority)
    db.add(sched)
    await db.flush()
    made = []
    for kw in specs:
        kw = dict(kw)
        created = kw.pop("created_at", LONG_AGO)
        kw.setdefault("recurrence_type", RecurrenceType.DAILY)
        block = ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, **kw)
        block.created_at = created
        db.add(block)
        made.append(block)
    await db.commit()
    return made


async def _on(db, station, *when, tz=NY):
    block = await SchedulingService(db).get_active_block_for_station(
        station.id, datetime(*when, tzinfo=tz).astimezone(UTC))
    return block.name if block else None


# ---------------------------------------------------------------- V1-65 ranking

@pytest.mark.asyncio
async def test_v1_65_monthly_beats_weekly_and_newer_wins_a_full_tie(db_session):
    """V1-65: at equal priority a monthly block beats a weekly one; two blocks equal in
    everything (type, length) are settled by the newer one; block priority beats type"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Weekly", "start_time": time(9), "end_time": time(10),
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["tuesday"]},
        {"name": "Monthly", "start_time": time(9), "end_time": time(10),
         "recurrence_type": RecurrenceType.MONTHLY, "recurrence_pattern": [17]},
        {"name": "Old", "start_time": time(12), "end_time": time(13), "created_at": LONG_AGO},
        {"name": "New", "start_time": time(12), "end_time": time(13),
         "created_at": LONG_AGO + timedelta(days=1)},
        {"name": "Daily priority", "start_time": time(15), "end_time": time(16), "priority": 1},
        {"name": "One-time", "start_time": time(15), "end_time": time(16),
         "recurrence_type": RecurrenceType.ONE_TIME, "start_date": date(2026, 11, 17)},
    ])
    assert await _on(db_session, station, 2026, 11, 17, 9, 30) == "Monthly"  # Tuesday the 17th
    assert await _on(db_session, station, 2026, 11, 24, 9, 30) == "Weekly"
    assert await _on(db_session, station, 2026, 11, 17, 12, 30) == "New"
    assert await _on(db_session, station, 2026, 11, 17, 15, 30) == "Daily priority"


@pytest.mark.asyncio
async def test_v1_65_schedule_priority_beats_block_priority(db_session):
    """V1-65: a block in a higher-priority schedule beats a higher-priority block in a
    lower schedule"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Loud block", "start_time": time(9), "end_time": time(10), "priority": 50}])
    await _blocks(db_session, station, [
        {"name": "Top schedule", "start_time": time(8), "end_time": time(12)}], priority=1)
    assert await _on(db_session, station, 2026, 11, 17, 9, 30) == "Top schedule"


# ---------------------------------------------------------------- V1-66/67 start-only

@pytest.mark.asyncio
async def test_v1_66_weekly_start_only_block_holds_all_week(db_session):
    """V1-66: a start-only block that starts Monday 06:00 stays on all week until the next
    start-only block starts, including past midnight and on days it is not selected"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Monday mix", "start_time": time(6), "end_time": time(6), "until_next": True,
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["monday"]},
        {"name": "Thursday mix", "start_time": time(20), "end_time": time(20), "until_next": True,
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["thursday"]},
    ])
    # 2026-11-16 is a Monday
    assert await _on(db_session, station, 2026, 11, 16, 5, 59) == "Thursday mix"
    assert await _on(db_session, station, 2026, 11, 16, 6, 0) == "Monday mix"
    assert await _on(db_session, station, 2026, 11, 18, 0, 30) == "Monday mix"
    assert await _on(db_session, station, 2026, 11, 19, 19, 59) == "Monday mix"
    assert await _on(db_session, station, 2026, 11, 19, 20, 0) == "Thursday mix"
    assert await _on(db_session, station, 2026, 11, 22, 23, 0) == "Thursday mix"


@pytest.mark.asyncio
async def test_v1_66_timed_block_over_running_playlist_wins_a_tie(db_session):
    """V1-66: a timed block at the same priority wins over the running playlist, and the
    running playlist comes back when it ends; a higher-priority running playlist wins"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Running", "start_time": time(5), "end_time": time(5), "until_next": True},
        {"name": "Shiur", "start_time": time(10), "end_time": time(11)},
    ])
    assert await _on(db_session, station, 2026, 11, 17, 9, 59) == "Running"
    assert await _on(db_session, station, 2026, 11, 17, 10, 30) == "Shiur"
    assert await _on(db_session, station, 2026, 11, 17, 11, 0) == "Running"

    other = await _station(db_session)
    await _blocks(db_session, other, [
        {"name": "Running", "start_time": time(5), "end_time": time(5), "until_next": True,
         "priority": 3},
        {"name": "Shiur", "start_time": time(10), "end_time": time(11)},
    ])
    assert await _on(db_session, other, 2026, 11, 17, 10, 30) == "Running"


@pytest.mark.asyncio
async def test_v1_66_start_only_block_looks_back_eight_days(db_session):
    """V1-66: a one-time start-only block is found up to 8 days after its start"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Once", "start_time": time(9), "end_time": time(9), "until_next": True,
         "recurrence_type": RecurrenceType.ONE_TIME, "start_date": date(2026, 11, 9)},
    ])
    assert await _on(db_session, station, 2026, 11, 9, 8, 59) is None
    assert await _on(db_session, station, 2026, 11, 9, 9, 0) == "Once"
    assert await _on(db_session, station, 2026, 11, 17, 12, 0) == "Once"  # 8 days later


@pytest.mark.asyncio
async def test_v1_67_start_only_block_saved_after_its_start_waits(db_session):
    """V1-67: saved at 6:40 for 6:52 takes over at 6:52; saved at 6:55 for 6:52 waits for
    tomorrow's 6:52 (yesterday's 6:52 does not count, it was before the block existed)"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Base", "start_time": time(5), "end_time": time(5), "until_next": True},
        {"name": "Early save", "start_time": time(6, 52), "end_time": time(6, 52),
         "until_next": True, "created_at": datetime(2026, 11, 17, 6, 40, tzinfo=NY).astimezone(UTC)},
    ])
    assert await _on(db_session, station, 2026, 11, 17, 6, 45) == "Base"
    assert await _on(db_session, station, 2026, 11, 17, 6, 52) == "Early save"

    other = await _station(db_session)
    await _blocks(db_session, other, [
        {"name": "Base", "start_time": time(5), "end_time": time(5), "until_next": True},
        {"name": "Late save", "start_time": time(6, 52), "end_time": time(6, 52),
         "until_next": True, "created_at": datetime(2026, 11, 17, 6, 55, tzinfo=NY).astimezone(UTC)},
    ])
    assert await _on(db_session, other, 2026, 11, 17, 7, 0) == "Base"
    assert await _on(db_session, other, 2026, 11, 18, 5, 30) == "Base"
    assert await _on(db_session, other, 2026, 11, 18, 6, 52) == "Late save"


# ---------------------------------------------------------------- V1-68 / V2-102 overnight

@pytest.mark.asyncio
async def test_v1_68_after_midnight_part_belongs_to_the_start_day(db_session):
    """V1-68, V2-102: a Friday-only 22:00-02:00 block airs Saturday 00:00-02:00 although
    Saturday is not selected, and does not air Sunday 00:00-02:00; the edges are exact"""
    station = await _station(db_session)
    await _blocks(db_session, station, [{
        "name": "Friday night", "start_time": time(22), "end_time": time(2),
        "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["friday"],
    }])
    # 2026-11-20 is a Friday
    assert await _on(db_session, station, 2026, 11, 20, 21, 59) is None
    assert await _on(db_session, station, 2026, 11, 20, 22, 0) == "Friday night"
    assert await _on(db_session, station, 2026, 11, 21, 0, 0) == "Friday night"
    assert await _on(db_session, station, 2026, 11, 21, 1, 59) == "Friday night"
    assert await _on(db_session, station, 2026, 11, 21, 2, 0) is None
    assert await _on(db_session, station, 2026, 11, 21, 22, 30) is None  # Saturday evening
    assert await _on(db_session, station, 2026, 11, 22, 1, 0) is None  # Sunday after midnight
    assert await _on(db_session, station, 2026, 11, 20, 1, 0) is None  # Thursday's night


@pytest.mark.asyncio
async def test_v1_68_saturday_selected_airs_sunday_morning_part(db_session):
    """V1-68, V2-102: with Friday and Saturday selected the block airs both nights; with
    only Saturday selected Saturday 00:00-02:00 belongs to Friday and stays off"""
    station = await _station(db_session)
    await _blocks(db_session, station, [{
        "name": "Weekend nights", "start_time": time(22), "end_time": time(2),
        "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["Friday", "Saturday"],
    }])
    assert await _on(db_session, station, 2026, 11, 21, 1, 0) == "Weekend nights"
    assert await _on(db_session, station, 2026, 11, 22, 1, 0) == "Weekend nights"
    assert await _on(db_session, station, 2026, 11, 23, 1, 0) is None

    other = await _station(db_session)
    await _blocks(db_session, other, [{
        "name": "Motzei Shabbos", "start_time": time(22), "end_time": time(2),
        "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["saturday"],
    }])
    assert await _on(db_session, other, 2026, 11, 21, 1, 0) is None
    assert await _on(db_session, other, 2026, 11, 21, 23, 0) == "Motzei Shabbos"
    assert await _on(db_session, other, 2026, 11, 22, 1, 0) == "Motzei Shabbos"


@pytest.mark.asyncio
async def test_v2_102_one_time_range_and_monthly_overnight(db_session):
    """V2-102: an overnight one-time block over a date range airs the morning after its last
    date and not the morning of its first date; a monthly overnight block on the 31st airs
    the morning of the 1st"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Week of shiurim", "start_time": time(23), "end_time": time(1),
         "recurrence_type": RecurrenceType.ONE_TIME,
         "start_date": date(2026, 12, 1), "end_date": date(2026, 12, 3)},
        {"name": "Month end", "start_time": time(22), "end_time": time(3),
         "recurrence_type": RecurrenceType.MONTHLY, "recurrence_pattern": [31]},
    ])
    assert await _on(db_session, station, 2026, 12, 1, 0, 30) is None
    assert await _on(db_session, station, 2026, 12, 2, 0, 30) == "Week of shiurim"
    assert await _on(db_session, station, 2026, 12, 4, 0, 30) == "Week of shiurim"
    assert await _on(db_session, station, 2026, 12, 5, 0, 30) is None
    assert await _on(db_session, station, 2027, 1, 1, 2, 0) == "Month end"
    assert await _on(db_session, station, 2026, 12, 31, 2, 0) is None


# ---------------------------------------------------------------- V1-69 recurrence

@pytest.mark.asyncio
async def test_v1_69_recurrence_types(db_session):
    """V1-69: weekly day names (any case) / empty list = every day; monthly day list and
    empty list; one-time start_date only = that day; daily every day"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Weekly", "start_time": time(6), "end_time": time(7),
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["Sunday", "WEDNESDAY"]},
        {"name": "Monthly", "start_time": time(8), "end_time": time(9),
         "recurrence_type": RecurrenceType.MONTHLY, "recurrence_pattern": [1, 15]},
        {"name": "Monthly any", "start_time": time(10), "end_time": time(11),
         "recurrence_type": RecurrenceType.MONTHLY, "recurrence_pattern": []},
        {"name": "One day", "start_time": time(12), "end_time": time(13),
         "recurrence_type": RecurrenceType.ONE_TIME, "start_date": date(2026, 11, 19)},
        {"name": "Daily", "start_time": time(14), "end_time": time(15)},
    ])
    assert await _on(db_session, station, 2026, 11, 22, 6, 30) == "Weekly"  # Sunday
    assert await _on(db_session, station, 2026, 11, 18, 6, 30) == "Weekly"  # Wednesday
    assert await _on(db_session, station, 2026, 11, 19, 6, 30) is None
    assert await _on(db_session, station, 2026, 12, 1, 8, 30) == "Monthly"
    assert await _on(db_session, station, 2026, 12, 15, 8, 30) == "Monthly"
    assert await _on(db_session, station, 2026, 12, 2, 8, 30) is None
    assert await _on(db_session, station, 2026, 12, 2, 10, 30) == "Monthly any"
    assert await _on(db_session, station, 2026, 11, 19, 12, 30) == "One day"
    assert await _on(db_session, station, 2026, 11, 20, 12, 30) is None
    assert await _on(db_session, station, 2031, 3, 4, 14, 30) == "Daily"


# ---------------------------------------------------------------- V1-70 sun without coordinates

@pytest.mark.asyncio
async def test_v1_70_station_without_coordinates_does_not_get_jerusalem_sunset(db_session):
    """V1-70: a New York station with no latitude/longitude used to get Jerusalem's sunset
    (09:38 New York time). It now uses the block's own clock time."""
    station = await _station(db_session)  # no latitude/longitude
    await _blocks(db_session, station, [{
        "name": "After sunset", "start_time": time(17), "end_time": time(23),
        "start_sun_event": SunEvent.SUNSET,
    }])
    assert await _on(db_session, station, 2026, 11, 17, 10, 0) is None
    assert await _on(db_session, station, 2026, 11, 17, 16, 50) is None
    assert await _on(db_session, station, 2026, 11, 17, 17, 0) == "After sunset"


@pytest.mark.asyncio
async def test_v1_70_israel_station_without_coordinates_uses_jerusalem(db_session):
    """V1-70: a station in Israel time with no coordinates still uses Jerusalem's sunset
    (about 16:38 on 17 Nov 2026), not its clock time of 18:00"""
    station = await _station(db_session, timezone="Asia/Jerusalem")
    await _blocks(db_session, station, [{
        "name": "After sunset", "start_time": time(18), "end_time": time(23),
        "start_sun_event": SunEvent.SUNSET,
    }])
    assert await _on(db_session, station, 2026, 11, 17, 16, 30, tz=JERUSALEM) is None
    assert await _on(db_session, station, 2026, 11, 17, 16, 45, tz=JERUSALEM) == "After sunset"


# ---------------------------------------------------------------- V2-103 offset crossing midnight

@pytest.mark.asyncio
async def test_v2_103_sun_offset_past_midnight_stays_on_the_right_night(db_session):
    """V2-103: Oslo sunset on Fri 18 Jun 2027 is 22:42; a Friday block from sunset + 90 min
    (00:12 Saturday) to 02:00 airs Saturday 00:12-02:00, not Friday 00:12-02:00"""
    station = await _station(db_session, latitude=59.91, longitude=10.75, timezone="Europe/Oslo")
    await _blocks(db_session, station, [{
        "name": "Late", "start_time": time(23), "end_time": time(2),
        "start_sun_event": SunEvent.SUNSET, "start_sun_offset": 90,
        "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["friday"],
    }])
    assert await _on(db_session, station, 2027, 6, 18, 23, 30, tz=OSLO) is None
    assert await _on(db_session, station, 2027, 6, 19, 0, 5, tz=OSLO) is None
    assert await _on(db_session, station, 2027, 6, 19, 0, 20, tz=OSLO) == "Late"
    assert await _on(db_session, station, 2027, 6, 19, 1, 50, tz=OSLO) == "Late"
    assert await _on(db_session, station, 2027, 6, 19, 2, 5, tz=OSLO) is None
    assert await _on(db_session, station, 2027, 6, 18, 0, 30, tz=OSLO) is None  # Thursday's night


@pytest.mark.asyncio
async def test_v2_103_sun_window_keeps_its_real_length(db_session):
    """V2-103: a block from sunset + 60 to sunset + 180 (23:42 to 01:42 in Oslo in June) is
    two hours long, on both sides of midnight, and not on during the day"""
    station = await _station(db_session, latitude=59.91, longitude=10.75, timezone="Europe/Oslo")
    await _blocks(db_session, station, [{
        "name": "Two hours", "start_time": time(21), "end_time": time(23),
        "start_sun_event": SunEvent.SUNSET, "start_sun_offset": 60,
        "end_sun_event": SunEvent.SUNSET, "end_sun_offset": 180,
    }])
    assert await _on(db_session, station, 2027, 6, 18, 12, 0, tz=OSLO) is None
    assert await _on(db_session, station, 2027, 6, 18, 23, 30, tz=OSLO) is None
    assert await _on(db_session, station, 2027, 6, 18, 23, 50, tz=OSLO) == "Two hours"
    assert await _on(db_session, station, 2027, 6, 19, 1, 30, tz=OSLO) == "Two hours"
    assert await _on(db_session, station, 2027, 6, 19, 1, 50, tz=OSLO) is None


@pytest.mark.asyncio
async def test_v2_103_sunset_offset_into_next_day_for_start_only_block(db_session):
    """V2-103: a start-only block at sunset + 90 (00:12 Saturday in Oslo) starts after
    midnight, not at 00:12 of the same Friday"""
    station = await _station(db_session, latitude=59.91, longitude=10.75, timezone="Europe/Oslo")
    await _blocks(db_session, station, [
        {"name": "Base", "start_time": time(5), "end_time": time(5), "until_next": True},
        {"name": "Night", "start_time": time(23), "end_time": time(23), "until_next": True,
         "start_sun_event": SunEvent.SUNSET, "start_sun_offset": 90,
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["friday"]},
    ])
    assert await _on(db_session, station, 2027, 6, 18, 1, 0, tz=OSLO) == "Base"
    assert await _on(db_session, station, 2027, 6, 18, 23, 59, tz=OSLO) == "Base"
    assert await _on(db_session, station, 2027, 6, 19, 0, 20, tz=OSLO) == "Night"
    assert await _on(db_session, station, 2027, 6, 19, 5, 0, tz=OSLO) == "Base"


# ---------------------------------------------------------------- V1-71 playback modes

async def _playlist(db, station, n=4):
    assets = [Asset(id=uuid.uuid4(), title=f"Song {i}", duration=200.0,
                    file_path=f"{uuid.uuid4().hex}.mp3", asset_type="music") for i in range(n)]
    db.add_all(assets)
    await db.commit()
    entries = [SimpleNamespace(asset_id=a.id, position=i, weight=1, is_enabled=True)
               for i, a in enumerate(assets)]
    return assets, entries


def _block(entries, mode):
    return SimpleNamespace(playlist_template_id=None, playlist_template=None,
                           playlist_entries=entries, playback_mode=mode, schedule_id=None)


async def _played(db, station, asset, minutes_ago):
    db.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id,
                   start_utc=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                   source=PlaySource.SCHEDULER))
    await db.commit()


@pytest.mark.asyncio
async def test_v1_71_sequential_continues_after_last_played_and_wraps(db_session):
    """V1-71: sequential starts at the first entry, continues after the last played one,
    wraps after the end and skips disabled entries"""
    station = await _station(db_session)
    assets, entries = await _playlist(db_session, station)
    svc = SchedulingService(db_session)
    block = _block(entries, PlaybackMode.SEQUENTIAL)
    assert await svc.get_next_asset_for_block(block, station.id) == assets[0].id
    await _played(db_session, station, assets[1], 5)
    assert await svc.get_next_asset_for_block(block, station.id) == assets[2].id
    await _played(db_session, station, assets[3], 1)
    assert await svc.get_next_asset_for_block(block, station.id) == assets[0].id
    entries[0].is_enabled = False
    assert await svc.get_next_asset_for_block(block, station.id) == assets[1].id
    for e in entries:
        e.is_enabled = False
    assert await svc.get_next_asset_for_block(block, station.id) is None


@pytest.mark.asyncio
async def test_v1_71_shuffle_avoids_the_last_half(db_session):
    """V1-71: shuffle never picks one of the last half (of the list length) played, and
    never a disabled entry"""
    station = await _station(db_session)
    assets, entries = await _playlist(db_session, station, n=6)
    entries[5].is_enabled = False
    await _played(db_session, station, assets[0], 3)
    await _played(db_session, station, assets[1], 2)
    svc = SchedulingService(db_session)
    block = _block(entries, PlaybackMode.SHUFFLE)
    random.seed(1)
    picks = {await svc.get_next_asset_for_block(block, station.id) for _ in range(40)}
    # 5 enabled -> the last 2 plays are avoided
    assert picks == {assets[2].id, assets[3].id, assets[4].id}


@pytest.mark.asyncio
async def test_v1_71_weighted_uses_weight_at_least_one(db_session):
    """V1-71: weighted picks follow the weights, a weight of 0 counts as 1, and disabled
    entries are never chosen"""
    station = await _station(db_session)
    assets, entries = await _playlist(db_session, station, n=3)
    entries[0].weight = 0
    entries[1].weight = 9
    entries[2].weight = 50
    entries[2].is_enabled = False
    svc = SchedulingService(db_session)
    block = _block(entries, PlaybackMode.WEIGHTED)
    random.seed(3)
    counts = Counter([await svc.get_next_asset_for_block(block, station.id) for _ in range(500)])
    assert assets[2].id not in counts
    assert counts[assets[0].id] > 0
    assert counts[assets[1].id] > 5 * counts[assets[0].id]


# ---------------------------------------------------------------- V1-72 template rotation

@pytest.mark.asyncio
async def test_v1_72_rotation_on_air_is_not_shifted_by_other_plays(db_session):
    """V1-72: the playlist generator (what goes on air) walks the template's slots with a
    per-block cursor, so time checks, weather and spots in the play log do not shift which
    slot comes next (the old SchedulingService path used total plays modulo slot count and
    is no longer called on air)"""
    from app.services.playlist_generator_service import generate

    station = await _station(db_session)
    template = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db_session.add(template)
    await db_session.flush()
    for i, cat in enumerate(["A", "B", "C"]):
        db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=template.id, position=i,
                                    asset_type="music", category=cat))
    [block] = await _blocks(db_session, station, [{
        "name": "Mix", "start_time": time(0), "end_time": time(23, 59),
        "playlist_template_id": template.id}])
    songs = [Asset(id=uuid.uuid4(), title=f"{cat}{n}", artist=f"Artist {cat}{n}", duration=240.0,
                   file_path=f"{uuid.uuid4().hex}.mp3", asset_type="music", category=cat)
             for cat in "ABC" for n in range(4)]
    jingle = Asset(id=uuid.uuid4(), title="Time", duration=10.0, file_path="t.mp3",
                   asset_type="jingle", category="time_announcement")
    db_session.add_all([*songs, jingle])
    await db_session.commit()
    for m in range(5):  # time checks / spots on the log
        await _played(db_session, station, jingle, 30 + m)

    start = datetime(2026, 11, 17, 9, 0, tzinfo=NY)
    plan = await generate(db_session, station, start, 0.4, artist_distance=0)
    assert [p.category for p in plan.picks[:4]] == ["A", "B", "C", "A"]
    plan = await generate(db_session, station, start, 0.4, artist_distance=0,
                          cursor={str(block.id): 1})
    assert [p.category for p in plan.picks[:3]] == ["B", "C", "A"]
