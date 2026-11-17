"""F-BLOCKS-HOUR-EDGE: a queue filled for the first time a minute or two before hh:00
(a new station, or a queue reset at hh:58) still gets the coming hour's time check
and weather, not only its program block."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

import app.services.queue_replenish_service as qrs
from app.config import settings
from app.models.queue_entry import QueueEntry
from app.services.queue_replenish_service import QueueReplenishService, local_hour_marks
from tests.test_blocks_keep_hourly import _station


def _frozen(at: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.astimezone(tz)
    return Frozen


@pytest.mark.parametrize("seconds_before", [120, 30, 1])
def test_the_coming_hour_is_a_mark(seconds_before):
    hour = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    now = hour - timedelta(seconds=seconds_before)
    assert local_hour_marks(now, timezone.utc, 48)[0] == hour


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds_before", [90, 5])
async def test_fill_at_hh58_queues_the_coming_time_check(db_session, monkeypatch, seconds_before):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _ = await _station(db_session, keep=True)
    await db_session.commit()
    hour = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    monkeypatch.setattr(qrs, "datetime", _frozen(hour - timedelta(seconds=seconds_before)))
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        svc = QueueReplenishService(db_session, station.id)
        svc.max_pos = 0
        await svc._load_automation_config()
        await svc._schedule_hourly_announcements()
        await db_session.commit()
    at_hour = (await db_session.execute(
        select(QueueEntry.asset_id).where(QueueEntry.station_id == station.id, QueueEntry.preempt_at == hour)
        .order_by(QueueEntry.position)
    )).scalars().all()
    assert list(at_hour)[:2] == [clock.id, weather.id], "the coming hour has no time check"
