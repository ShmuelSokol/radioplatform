"""Live shows found broken on 2026-11-16.

- A show started after its planned end was hard-stopped at once, and the stop was
  never saved (the scheduler pass ends with a rollback), so it was "hard stopped"
  again every few seconds while the show stayed live.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.live_show import LiveShow, LiveShowStatus
from app.models.station import Station
from app.services import live_show_service
from app.services.scheduler_engine import SchedulerEngine
from tests.conftest import TestSessionLocal


async def _show(db, start, end):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db.add(station)
    await db.flush()
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="test SS",
                    status=LiveShowStatus.SCHEDULED, scheduled_start=start, scheduled_end=end)
    db.add(show)
    await db.commit()
    return station, show


@pytest.mark.asyncio
async def test_show_started_late_gets_its_planned_length(db_session):
    now = datetime.now(UTC)
    _, show = await _show(db_session, now - timedelta(hours=5, minutes=30), now - timedelta(hours=5))
    started = await live_show_service.start_show(db_session, show.id)
    end = started.scheduled_end if started.scheduled_end.tzinfo else started.scheduled_end.replace(tzinfo=UTC)
    assert timedelta(minutes=29) < end - now < timedelta(minutes=31)


@pytest.mark.asyncio
async def test_show_with_no_end_gets_an_hour(db_session):
    now = datetime.now(UTC)
    _, show = await _show(db_session, None, None)
    started = await live_show_service.start_show(db_session, show.id)
    end = started.scheduled_end if started.scheduled_end.tzinfo else started.scheduled_end.replace(tzinfo=UTC)
    assert timedelta(minutes=59) < end - now < timedelta(minutes=61)


@pytest.mark.asyncio
async def test_hard_stop_is_saved(db_session):
    now = datetime.now(UTC)
    station, show = await _show(db_session, now - timedelta(hours=1), now + timedelta(hours=1))
    await live_show_service.start_show(db_session, show.id)
    started = await db_session.get(LiveShow, show.id)
    started.scheduled_end = now - timedelta(seconds=5)  # its time is up
    await db_session.commit()

    async with TestSessionLocal() as db:
        st = await db.get(Station, station.id)
        await SchedulerEngine()._check_live_show_hard_stop(
            db, st, str(show.id), datetime.now(UTC))
        await db.rollback()  # what the end of every scheduler pass does

    async with TestSessionLocal() as db:
        saved = (await db.execute(select(LiveShow).where(LiveShow.id == show.id))).scalar_one()
        assert saved.status == LiveShowStatus.ENDED
        st = await db.get(Station, station.id)
        assert "live_show_id" not in (st.automation_config or {})


# ── A show never runs into Shabbos or Yom Tov (station simulator, 2026-11-20) ───────

async def _blackout(db, station, start, end, stations=None):
    from app.models.holiday_window import HolidayWindow

    db.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start, end_datetime=end,
                         is_blackout=True, affected_stations=stations))
    await db.commit()


@pytest.mark.asyncio
async def test_a_show_ends_before_candle_lighting(db_session):
    """Planned to run 40 minutes with candle lighting in 18: its end moves to 30 s
    before, so the countdown, the delay's wind down and the hard stop all follow."""
    now = datetime.now(UTC)
    station, show = await _show(db_session, now, now + timedelta(minutes=40))
    candles = now + timedelta(minutes=18)
    await _blackout(db_session, station, candles, candles + timedelta(hours=25))
    started = await live_show_service.start_show(db_session, show.id)
    end = started.scheduled_end if started.scheduled_end.tzinfo else started.scheduled_end.replace(tzinfo=UTC)
    assert end == candles - live_show_service.BEFORE_BLACKOUT


@pytest.mark.asyncio
async def test_another_stations_shabbos_does_not_shorten_a_show(db_session):
    now = datetime.now(UTC)
    station, show = await _show(db_session, now, now + timedelta(minutes=40))
    candles = now + timedelta(minutes=18)
    await _blackout(db_session, station, candles, candles + timedelta(hours=25),
                    stations={"station_ids": [str(uuid.uuid4())]})
    started = await live_show_service.start_show(db_session, show.id)
    end = started.scheduled_end if started.scheduled_end.tzinfo else started.scheduled_end.replace(tzinfo=UTC)
    assert timedelta(minutes=39) < end - now < timedelta(minutes=41)
    assert await live_show_service.off_air_now(db_session, station.id) is None


@pytest.mark.asyncio
async def test_no_show_starts_on_shabbos(db_session):
    """The host's microphone opening does not start a show while the station is off
    the air, and the scheduler stops one that is on when Shabbos begins."""
    now = datetime.now(UTC)
    station, show = await _show(db_session, now, now + timedelta(minutes=40))
    await _blackout(db_session, station, now - timedelta(hours=1), now + timedelta(hours=24))
    assert await live_show_service.off_air_now(db_session, station.id) == "Shabbos"
    assert await live_show_service.start_if_scheduled(db_session, show.id) is False
    assert (await db_session.get(LiveShow, show.id)).status == LiveShowStatus.SCHEDULED


@pytest.mark.asyncio
async def test_a_show_on_air_when_shabbos_begins_is_stopped(db_session):
    """A window added after the show began (its end was not moved): the scheduler
    stops the show all the same."""
    now = datetime.now(UTC)
    station, show = await _show(db_session, now, now + timedelta(minutes=40))
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    await _blackout(db_session, station, now - timedelta(seconds=1), now + timedelta(hours=25))

    async with TestSessionLocal() as db:
        st = await db.get(Station, station.id)
        await SchedulerEngine()._check_live_show_hard_stop(db, st, str(show.id), datetime.now(UTC))
        await db.rollback()
    async with TestSessionLocal() as db:
        saved = (await db.execute(select(LiveShow).where(LiveShow.id == show.id))).scalar_one()
        assert saved.status == LiveShowStatus.ENDED
