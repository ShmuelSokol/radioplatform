"""A queue refill never plans sponsorship slots that it then throws away.

Review thread, 2026-11-18: with the spot recordings marked do-not-play every queue
read removed the 19 pending sponsorship slots ("removed 19 placeholder entries") and
planned them again ("Spot slots reconciled +19 -0 (kept 0)"): about 12 dead queue rows
a second, for ever. The planner now leaves out a sponsorship whose recording no
automatic pick may use (do-not-play, placeholder, rejected in review).
"""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService
from app.services.spot_schedule_service import reconcile_spot_slots

# Slot counts depend on the minute; see clock_off_the_slots in conftest.py
pytestmark = pytest.mark.usefixtures("clock_off_the_slots")


async def _station_with_contract(db: AsyncSession, **asset_fields):
    station = Station(id=uuid.uuid4(), name=f"Main {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      is_active=True)
    spot = Asset(id=uuid.uuid4(), title="Pita Hut", duration=30.0, file_path="https://cdn.example.com/p.mp3",
                 asset_type="spot", **asset_fields)
    sponsor = Sponsor(id=uuid.uuid4(), name="Pita Hut", length_seconds=30, priority=1, audio_file_path="p.mp3")
    db.add_all([station, spot, sponsor])
    await db.flush()
    contract = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                            days_of_week="0,1,2,3,4,5,6", dayparts="morning,afternoon,evening,overnight",
                            plays_per_daypart=1, is_active=True)
    db.add(contract)
    await db.commit()
    return station, spot


async def _refill(db: AsyncSession, station) -> dict | None:
    svc = QueueReplenishService(db, station.id)
    svc.max_pos = 0
    await svc._load_automation_config()
    await svc._schedule_ad_slots()
    await db.commit()


async def _rows(db: AsyncSession, station) -> list[uuid.UUID]:
    return sorted((await db.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == station.id)
    )).scalars().all(), key=str)


@pytest.mark.parametrize("fields", [
    {"category": "do_not_play"},
    {"category": " Do_Not_Play "},
    {"review_status": "rejected"},
], ids=["do-not-play", "do-not-play spelled loosely", "rejected in review"])
@pytest.mark.asyncio
async def test_a_recording_that_may_not_air_is_not_planned_and_thrown_away_again(db_session, fields):
    station, spot = await _station_with_contract(db_session, **fields)
    for _ in range(3):
        result = await reconcile_spot_slots(db_session, station.id, datetime.now(UTC))
        await db_session.commit()
        assert result is not None and result["added"] == 0, result
        await _refill(db_session, station)
        assert await _rows(db_session, station) == []


@pytest.mark.asyncio
async def test_marking_a_recording_do_not_play_clears_its_slots_once(db_session):
    station, spot = await _station_with_contract(db_session)
    await _refill(db_session, station)
    planned = await _rows(db_session, station)
    assert planned, "the sponsorship was not planned at all"
    # Nothing changes: the same rows stay (no slot is made twice)
    await _refill(db_session, station)
    assert await _rows(db_session, station) == planned

    spot.category = "do_not_play"
    await db_session.commit()
    await _refill(db_session, station)
    assert await _rows(db_session, station) == []
    # And every later refill makes and removes nothing
    before = (await db_session.execute(select(func.count()).select_from(QueueEntry))).scalar()
    for _ in range(3):
        await _refill(db_session, station)
    assert (await db_session.execute(select(func.count()).select_from(QueueEntry))).scalar() == before

    # Back on: it is planned again, once
    spot.category = "Spots"
    await db_session.commit()
    await _refill(db_session, station)
    again = await _rows(db_session, station)
    assert len(again) == len(planned)
    await _refill(db_session, station)
    assert await _rows(db_session, station) == again


@pytest.mark.asyncio
async def test_spots_mode_off_plans_nothing_and_does_not_churn(db_session):
    station, spot = await _station_with_contract(db_session)
    await _refill(db_session, station)
    assert await _rows(db_session, station)
    station.automation_config = {"spots_on": False}
    await db_session.commit()
    for _ in range(3):
        result = await reconcile_spot_slots(db_session, station.id, datetime.now(UTC))
        await db_session.commit()
        assert result is not None and result["added"] == 0, result
        await _refill(db_session, station)
        assert await _rows(db_session, station) == []
