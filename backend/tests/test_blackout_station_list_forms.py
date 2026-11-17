"""Blackout station-list forms: {"station_ids": null} and {} mean every station."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.holiday_window import HolidayWindow
from app.schemas.holiday import HolidayWindowCreate, HolidayWindowUpdate
from app.services.station_service import window_covers_station


def _w(affected):
    now = datetime.now(UTC)
    return HolidayWindow(id=uuid.uuid4(), name="x", start_datetime=now, end_datetime=now + timedelta(hours=1),
                         is_blackout=True, affected_stations=affected)


def test_null_station_list_is_saved_as_every_station():
    """V1-89 follow-up: a blackout sent with station_ids null is stored as "all stations"
    (null), so the blackout checks never fail on it and keep the station silent."""
    now = datetime.now(UTC)
    body = dict(name="x", start_datetime=now, end_datetime=now + timedelta(hours=1))
    assert HolidayWindowCreate(**body, affected_stations={"station_ids": None}).affected_stations is None
    assert HolidayWindowCreate(**body, affected_stations={}).affected_stations is None
    assert HolidayWindowUpdate(affected_stations={"station_ids": None}).affected_stations is None
    kept = HolidayWindowCreate(**body, affected_stations={"station_ids": ["a"]}).affected_stations
    assert kept == {"station_ids": ["a"]}


@pytest.mark.parametrize("affected,covers", [
    (None, True), ({"station_ids": None}, True), ({}, True),
    ({"station_ids": []}, False), ({"station_ids": ["S1"]}, True), ({"station_ids": ["S2"]}, False),
])
def test_window_covers_station_reads_every_form(affected, covers):
    """V1-89 follow-up: every stored form gives an answer, none raises."""
    assert window_covers_station(_w(affected), "S1") is covers


@pytest.mark.asyncio
async def test_queue_and_scheduler_checks_survive_a_null_list(db_session):
    """V1-89 follow-up: the queue and scheduler blackout checks read a stored
    {"station_ids": null} as every station instead of raising."""
    from app.api.v1.queue import _is_blacked_out
    from app.models.station import Station
    from app.services.scheduler_engine import SchedulerEngine

    now = datetime.now(UTC)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="raw", start_datetime=now - timedelta(minutes=5),
                                 end_datetime=now + timedelta(hours=1), is_blackout=True,
                                 affected_stations={"station_ids": None}))
    st = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="UTC")
    db_session.add(st)
    await db_session.flush()
    assert await _is_blacked_out(db_session, st.id) is True
    assert await SchedulerEngine()._is_station_blacked_out(db_session, st, now) is True
