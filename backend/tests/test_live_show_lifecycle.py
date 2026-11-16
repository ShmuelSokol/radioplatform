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
