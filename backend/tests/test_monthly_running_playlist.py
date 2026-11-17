"""A monthly "running playlist" (start-only, until_next) holds all month until another
start-only block starts; it used to drop off 8 days after its start and plain music
played. A one-time start-only block with a date range holds until the range ends."""
import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.scheduling import SchedulingService

NY = ZoneInfo("America/New_York")
MADE = datetime(2020, 1, 1, tzinfo=UTC)


async def _station(db):
    station = Station(id=uuid.uuid4(), name=f"Monthly {uuid.uuid4().hex[:4]}",
                      timezone="America/New_York", is_active=True)
    db.add(station)
    await db.commit()
    return station


async def _blocks(db, station, specs):
    sched = Schedule(id=uuid.uuid4(), station_id=station.id, name="S", is_active=True)
    db.add(sched)
    await db.flush()
    made = []
    for kw in specs:
        kw = dict(kw)
        created = kw.pop("created_at", MADE)
        kw.setdefault("recurrence_type", RecurrenceType.DAILY)
        block = ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, **kw)
        block.created_at = created
        db.add(block)
        made.append(block)
    await db.commit()
    return made


def _running(name, day_of_month, hour=6, **kw):
    return {"name": name, "start_time": time(hour), "end_time": time(hour), "until_next": True,
            "recurrence_type": RecurrenceType.MONTHLY, "recurrence_pattern": [day_of_month], **kw}


async def _on(db, station, *when):
    block = await SchedulingService(db).get_active_block_for_station(
        station.id, datetime(*when, tzinfo=NY).astimezone(UTC))
    return block.name if block else None


@pytest.mark.asyncio
async def test_monthly_running_playlist_holds_all_month(db_session):
    """V1-66 monthly: a running playlist that starts on the 1st at 06:00 is on every day
    of the month (it dropped off on the 9th) until its next start on the 1st"""
    station = await _station(db_session)
    await _blocks(db_session, station, [_running("Monthly mix", 1)])
    assert await _on(db_session, station, 2026, 11, 1, 5, 59) == "Monthly mix"  # October's start
    assert await _on(db_session, station, 2026, 11, 1, 6, 0) == "Monthly mix"
    assert await _on(db_session, station, 2026, 11, 9, 12, 0) == "Monthly mix"
    assert await _on(db_session, station, 2026, 11, 20, 12, 0) == "Monthly mix"
    assert await _on(db_session, station, 2026, 11, 30, 23, 59) == "Monthly mix"
    assert await _on(db_session, station, 2026, 12, 1, 5, 59) == "Monthly mix"


@pytest.mark.asyncio
async def test_monthly_running_playlists_hand_over_on_their_days(db_session):
    """V1-66 monthly: two monthly running playlists (1st and 15th) each hold until the
    other starts"""
    station = await _station(db_session)
    await _blocks(db_session, station, [_running("First", 1), _running("Fifteenth", 15, hour=9)])
    assert await _on(db_session, station, 2026, 11, 14, 23, 0) == "First"
    assert await _on(db_session, station, 2026, 11, 15, 8, 59) == "First"
    assert await _on(db_session, station, 2026, 11, 15, 9, 0) == "Fifteenth"
    assert await _on(db_session, station, 2026, 11, 30, 12, 0) == "Fifteenth"
    assert await _on(db_session, station, 2026, 12, 1, 6, 0) == "First"
    assert await _on(db_session, station, 2026, 12, 10, 6, 0) == "First"


@pytest.mark.asyncio
async def test_monthly_31st_holds_through_months_without_a_31st(db_session):
    """V1-66 monthly: a running playlist on the 31st holds through February (no 31st)
    and into March, 59 days after its January start"""
    station = await _station(db_session)
    await _blocks(db_session, station, [_running("Month end", 31)])
    assert await _on(db_session, station, 2027, 2, 28, 12, 0) == "Month end"
    assert await _on(db_session, station, 2027, 3, 30, 23, 0) == "Month end"  # from 31 Jan
    assert await _on(db_session, station, 2027, 3, 31, 6, 0) == "Month end"


@pytest.mark.asyncio
async def test_monthly_running_playlist_counts_only_starts_after_it_was_made(db_session):
    """V1-66 monthly: made on the 10th, a running playlist for the 1st waits for the next
    1st (the 1st before it was made does not count, V1-67)"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Base", "start_time": time(5), "end_time": time(5), "until_next": True,
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["sunday"]},
        _running("Monthly mix", 1, created_at=datetime(2026, 11, 10, 12, tzinfo=NY).astimezone(UTC)),
    ])
    assert await _on(db_session, station, 2026, 11, 20, 12, 0) == "Base"
    assert await _on(db_session, station, 2026, 12, 1, 5, 59) == "Base"
    assert await _on(db_session, station, 2026, 12, 1, 6, 0) == "Monthly mix"
    # 2026-12-06 is a Sunday: the weekly running playlist starts again and takes over
    assert await _on(db_session, station, 2026, 12, 6, 5, 0) == "Base"


@pytest.mark.asyncio
async def test_timed_block_over_monthly_running_playlist_wins_a_tie(db_session):
    """V1-66 monthly: a timed block at the same priority plays over the monthly running
    playlist 20 days after it started, and the playlist comes back when it ends"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        _running("Monthly mix", 1),
        {"name": "Shiur", "start_time": time(10), "end_time": time(11)},
    ])
    assert await _on(db_session, station, 2026, 11, 21, 9, 59) == "Monthly mix"
    assert await _on(db_session, station, 2026, 11, 21, 10, 30) == "Shiur"
    assert await _on(db_session, station, 2026, 11, 21, 11, 0) == "Monthly mix"


@pytest.mark.asyncio
async def test_one_time_range_holds_until_the_range_ends(db_session):
    """V1-66 monthly: a one-time running playlist for 2-20 November is on from 09:00 on
    the 2nd until the end of the 20th (more than 8 days), then the monthly playlist
    that started before it is back"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        _running("Monthly mix", 1),
        {"name": "Campaign", "start_time": time(9), "end_time": time(9), "until_next": True,
         "recurrence_type": RecurrenceType.ONE_TIME,
         "start_date": date(2026, 11, 2), "end_date": date(2026, 11, 20)},
    ])
    assert await _on(db_session, station, 2026, 11, 2, 8, 59) == "Monthly mix"
    assert await _on(db_session, station, 2026, 11, 2, 9, 0) == "Campaign"
    assert await _on(db_session, station, 2026, 11, 15, 3, 0) == "Campaign"
    assert await _on(db_session, station, 2026, 11, 20, 23, 59) == "Campaign"
    assert await _on(db_session, station, 2026, 11, 21, 0, 0) == "Monthly mix"
    assert await _on(db_session, station, 2026, 11, 25, 12, 0) == "Monthly mix"


@pytest.mark.asyncio
async def test_weekly_and_single_day_running_playlists_unchanged(db_session):
    """V1-66 monthly: weekly running playlists still hand over by day of the week, and a
    one-day one-time running playlist is still held 8 days, not longer"""
    station = await _station(db_session)
    await _blocks(db_session, station, [
        {"name": "Once", "start_time": time(9), "end_time": time(9), "until_next": True,
         "recurrence_type": RecurrenceType.ONE_TIME, "start_date": date(2026, 11, 9)},
    ])
    assert await _on(db_session, station, 2026, 11, 17, 12, 0) == "Once"
    assert await _on(db_session, station, 2026, 11, 18, 12, 0) is None

    other = await _station(db_session)
    await _blocks(db_session, other, [
        {"name": "Monday", "start_time": time(6), "end_time": time(6), "until_next": True,
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["monday"]},
        {"name": "Thursday", "start_time": time(20), "end_time": time(20), "until_next": True,
         "recurrence_type": RecurrenceType.WEEKLY, "recurrence_pattern": ["thursday"]},
    ])
    assert await _on(db_session, other, 2026, 11, 18, 12, 0) == "Monday"
    assert await _on(db_session, other, 2026, 11, 21, 12, 0) == "Thursday"


def test_monthly_start_days_are_computed_not_walked():
    """V1-66 monthly: the previous start is worked out directly (a handful of candidate
    days), not by walking back up to 62 days on every song pick"""
    block = ScheduleBlock(id=uuid.uuid4(), name="x", start_time=time(6), end_time=time(6),
                          until_next=True, recurrence_type=RecurrenceType.MONTHLY,
                          recurrence_pattern=[31])
    days = list(SchedulingService._start_days(block, date(2027, 3, 15)))
    assert days == [date(2027, 1, 31)]
    block.recurrence_pattern = [1, 15]
    days = list(SchedulingService._start_days(block, date(2026, 11, 20)))
    assert days[:2] == [date(2026, 11, 15), date(2026, 11, 1)]
    assert len(days) <= 6


@pytest.mark.asyncio
async def test_look_ahead_shows_monthly_running_playlist_all_month(db_session):
    """V1-66 monthly: the look-ahead shows the monthly running playlist on the air on the
    20th, with no "nothing scheduled" gap"""
    from app.services.schedule_lookahead_service import look_ahead

    station = await _station(db_session)
    await _blocks(db_session, station, [_running("Monthly mix", 1)])
    result = await look_ahead(db_session, station, days=2,
                              now=datetime(2026, 11, 20, 12, tzinfo=NY).astimezone(UTC))
    kinds = [(f["kind"], f["title"]) for f in result["findings"]]
    assert ("plays", '"Monthly mix"') in kinds
    assert not any(k == "gap" for k, _ in kinds)


@pytest.mark.asyncio
async def test_realign_sees_timed_block_over_monthly_running_playlist(db_session):
    """V1-66 monthly: 15 minutes before a timed block with a playlist starts over a
    monthly running playlist (20 days into the month), the songs are redrawn; with no
    change ahead they are not"""
    from app.services import schedule_refresh_service as refresh

    station = await _station(db_session)
    templates = []
    for name in ("Monthly", "Shiur"):
        t = PlaylistTemplate(id=uuid.uuid4(), name=name, station_id=station.id, is_active=True)
        db_session.add(t)
        await db_session.flush()
        db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=t.id, position=0, asset_type="music"))
        templates.append(t)
    await db_session.commit()
    await _blocks(db_session, station, [
        _running("Monthly mix", 1, playlist_template_id=templates[0].id),
        {"name": "Shiur", "start_time": time(10), "end_time": time(11),
         "playlist_template_id": templates[1].id},
    ])
    refresh._realigned.pop(str(station.id), None)
    quiet = datetime(2026, 11, 21, 8, 0, tzinfo=NY).astimezone(UTC)
    assert await refresh.realign_before_block_change(db_session, station, quiet) is False
    before = datetime(2026, 11, 21, 9, 50, tzinfo=NY).astimezone(UTC)
    assert await refresh.realign_before_block_change(db_session, station, before) is True
    refresh._realigned.pop(str(station.id), None)
