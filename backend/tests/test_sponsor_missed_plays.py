"""Sponsors see their missed plays in the portal (Shmuel 2026-11-17: "Sponsor should
see missing"): plays promised that did not air, and coming plays with no room."""
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog, PlaySource
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import UserRole
from app.services.sponsor_delivery import delivery_for_sponsor
from tests.test_checklist_pinned_scheduling import API, _login

NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 10)  # a Tuesday
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=NY).astimezone(UTC)  # Thursday 3 PM


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY).astimezone(UTC)


async def _contract(db: AsyncSession, user_id=None, **fields):
    station = Station(id=uuid.uuid4(), name="Kol B'ramah Main", timezone="America/New_York", is_active=True)
    sponsor = Sponsor(id=uuid.uuid4(), name="TEST - Pita Hut", length_seconds=30, priority=1,
                      audio_file_path="", user_id=user_id)
    db.add_all([station, sponsor])
    await db.flush()
    spot = Asset(id=uuid.uuid4(), title="Pita Hut", file_path="p.mp3", duration=30.0, asset_type="spot")
    db.add(spot)
    await db.flush()
    values = dict(dayparts="morning,evening", plays_per_daypart=2, start_date=DAY, end_date=DAY + timedelta(days=1))
    values.update(fields)
    schedule = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                            name="TEST contract", created_at=_at(DAY - timedelta(days=3), 9), **values)
    db.add(schedule)
    await db.commit()
    return station, sponsor, spot, schedule


def _play(db: AsyncSession, station, spot, at: datetime):
    db.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, start_utc=at,
                   end_utc=at + timedelta(seconds=30), source=PlaySource.AD))


def _past(rows):
    return {(r.local_date, r.daypart): (r.promised, r.played, r.missed) for r in rows if r.when == "past"}


@pytest.mark.asyncio
async def test_past_plays_that_did_not_air_are_listed(db_session: AsyncSession):
    station, sponsor, spot, _ = await _contract(db_session)
    # Tuesday: both morning plays, one evening play. Wednesday: nothing in the morning,
    # both evening plays, one of them starting a few minutes after midnight (a soft play).
    for at in (_at(DAY, 8, 15), _at(DAY, 10, 30), _at(DAY, 19, 30),
               _at(DAY + timedelta(days=1), 20, 30), _at(DAY + timedelta(days=2), 0, 4)):
        _play(db_session, station, spot, at)
    await db_session.commit()
    rows = await delivery_for_sponsor(db_session, sponsor, now=NOW)
    assert _past(rows) == {
        (DAY, "evening"): (2, 1, 1),
        (DAY + timedelta(days=1), "morning"): (2, 0, 2),
    }
    # newest first, and nothing outside the contract's dates or times of day
    assert [r.local_date for r in rows if r.when == "past"] == [DAY + timedelta(days=1), DAY]


@pytest.mark.asyncio
async def test_a_play_counts_once_and_only_on_its_station(db_session: AsyncSession):
    station, sponsor, spot, _ = await _contract(db_session, dayparts="morning", plays_per_daypart=1,
                                                end_date=DAY)
    other = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    db_session.add(other)
    await db_session.flush()
    _play(db_session, other, spot, _at(DAY, 9))  # aired on another station: not this contract's play
    await db_session.commit()
    rows = await delivery_for_sponsor(db_session, sponsor, now=NOW)
    assert _past(rows) == {(DAY, "morning"): (1, 0, 1)}
    assert rows[0].station_name == "Kol B'ramah Main" and rows[0].asset_title == "Pita Hut"


@pytest.mark.asyncio
async def test_fixed_times_days_off_air_and_running_time_of_day(db_session: AsyncSession):
    """Exact times are each a promise; Shabbos is not; the time of day still running is not judged yet."""
    station, sponsor, spot, _ = await _contract(
        db_session, fixed_times="08:15,18:45,22:45", dayparts="morning", plays_per_daypart=1,
        start_date=DAY, end_date=DAY + timedelta(days=2))
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Test day off", is_blackout=True,
                                 start_datetime=_at(DAY + timedelta(days=1), 0),
                                 end_datetime=_at(DAY + timedelta(days=2), 0)))
    _play(db_session, station, spot, _at(DAY, 8, 16))
    _play(db_session, station, spot, _at(DAY, 18, 50))
    await db_session.commit()
    rows = await delivery_for_sponsor(db_session, sponsor, now=NOW)
    # Tuesday evening: 6:45 aired, 10:45 did not. Wednesday off air. Thursday morning
    # (8:15) is over and missed; Thursday afternoon is still running at 3 PM.
    assert _past(rows) == {(DAY, "evening"): (2, 1, 1), (DAY + timedelta(days=2), "morning"): (1, 0, 1)}


@pytest.mark.asyncio
async def test_nothing_before_the_contract_was_entered_or_when_cancelled(db_session: AsyncSession):
    station, sponsor, spot, schedule = await _contract(db_session, start_date=None, end_date=None,
                                                       dayparts="morning", plays_per_daypart=1)
    schedule.created_at = _at(DAY + timedelta(days=1), 9)  # entered Wednesday 9 AM
    await db_session.commit()
    rows = await delivery_for_sponsor(db_session, sponsor, now=NOW)
    assert _past(rows) == {(DAY + timedelta(days=2), "morning"): (1, 0, 1)}  # only Thursday morning
    schedule.is_active = False
    await db_session.commit()
    assert await delivery_for_sponsor(db_session, sponsor, now=NOW) == []


@pytest.mark.asyncio
async def test_coming_plays_with_no_room_are_listed(db_session: AsyncSession):
    """Three slots an hour: 30 plays in a morning cannot fit."""
    station, sponsor, spot, _ = await _contract(db_session, dayparts="morning", plays_per_daypart=30,
                                                start_date=DAY + timedelta(days=3), end_date=DAY + timedelta(days=3))
    rows = await delivery_for_sponsor(db_session, sponsor, now=NOW)
    coming = [r for r in rows if r.when == "coming"]
    assert len(coming) == 1 and coming[0].local_date == DAY + timedelta(days=3)
    assert coming[0].promised == 30 and coming[0].missed == 12  # 18 slots from 6 AM to noon
    assert "No room" in coming[0].note


@pytest.mark.asyncio
async def test_portal_endpoint_shows_only_my_missed_plays(client: AsyncClient, db_session: AsyncSession):
    user, headers = await _login(client, db_session, UserRole.SPONSOR)
    today = datetime.now(UTC).astimezone(NY).date()
    station, sponsor, spot, schedule = await _contract(
        db_session, user_id=user.id, dayparts="morning,afternoon,evening,overnight", plays_per_daypart=1,
        start_date=today - timedelta(days=2), end_date=today - timedelta(days=2))
    schedule.created_at = datetime.now(UTC) - timedelta(days=5)
    # somebody else's contract, also unplayed
    other = Sponsor(id=uuid.uuid4(), name="Other", length_seconds=30, priority=1, audio_file_path="")
    db_session.add(other)
    await db_session.flush()
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=other.id, asset_id=spot.id, station_id=station.id,
                                dayparts="morning", plays_per_daypart=3,
                                start_date=today - timedelta(days=2), end_date=today - timedelta(days=2),
                                created_at=datetime.now(UTC) - timedelta(days=5)))
    await db_session.commit()
    r = await client.get(f"{API}/sponsor-portal/missed-plays", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 14 and body["missed_past"] == 4 and body["missed_coming"] == 0
    assert {row["daypart"] for row in body["rows"]} == {"morning", "afternoon", "evening", "overnight"}
    assert all(row["when"] == "past" and row["note"] == "Did not air" for row in body["rows"])
    # staff and signed-out callers are refused
    assert (await client.get(f"{API}/sponsor-portal/missed-plays")).status_code in (401, 403)
