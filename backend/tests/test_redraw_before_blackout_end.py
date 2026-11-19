"""Songs after Shabbos are drawn again just before it ends (schedule_refresh_service)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.services import queue_replenish_service, schedule_refresh_service
from app.services.schedule_refresh_service import redraw_before_blackout_end


@pytest.mark.asyncio
async def test_redrawn_once_shortly_before_the_end(db_session: AsyncSession, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True,
                      automation_config={})
    other = Station(id=uuid.uuid4(), name="Elsewhere", timezone="America/New_York", is_active=True,
                    automation_config={})
    end = datetime(2026, 11, 21, 22, 47, 47, tzinfo=UTC)
    db_session.add_all([station, other, HolidayWindow(
        id=uuid.uuid4(), name="Shabbos", start_datetime=end - timedelta(hours=25, minutes=30),
        end_datetime=end, is_blackout=True, affected_stations={"station_ids": [str(station.id)]})])
    await db_session.commit()
    schedule_refresh_service._blackout_redrawn.clear()

    async def uses(db, station_id):
        return True

    calls = []

    async def replenish(self):
        calls.append((self.station_id, self.replace_all, self.keep_next))

    monkeypatch.setattr(schedule_refresh_service, "uses_block_playlists", uses)
    monkeypatch.setattr(queue_replenish_service.QueueReplenishService, "replenish", replenish)

    # an hour before the end: nothing yet
    assert not await redraw_before_blackout_end(db_session, station, end - timedelta(hours=1))
    # 14 minutes before: every waiting song is drawn again, once
    assert await redraw_before_blackout_end(db_session, station, end - timedelta(minutes=14))
    assert calls == [(station.id, True, 0)]
    assert not await redraw_before_blackout_end(db_session, station, end - timedelta(minutes=5))
    # a station the window does not cover, and after the end
    assert not await redraw_before_blackout_end(db_session, other, end - timedelta(minutes=14))
    assert not await redraw_before_blackout_end(db_session, station, end + timedelta(seconds=1))
    assert len(calls) == 1
