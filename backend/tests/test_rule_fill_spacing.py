"""A station that fills its queue from schedule rules (All Day Stories) and has
fewer items than the queue is long: the same item must not come back soon."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.schedule_rule import ScheduleRule
from app.models.station import Station
from app.models.user import UserRole
from app.services.queue_replenish_service import QueueReplenishService
from tests.test_admin_crud import _headers
from tests.test_playlist_generation import _closest_repeat

STORIES = 20


async def _stories_station(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name=f"Stories {uuid.uuid4().hex[:6]}", timezone="America/New_York", is_active=True)
    db.add(station)
    await db.flush()
    db.add(
        ScheduleRule(
            id=uuid.uuid4(), name="All day stories", rule_type="daypart", asset_type="story_spacing",
            hour_start=0, hour_end=24, days_of_week="0,1,2,3,4,5,6", is_active=True, priority=10,
            station_id=station.id,
        )
    )
    for n in range(STORIES):
        db.add(
            Asset(
                id=uuid.uuid4(), title=f"Story {n}", artist="Teller", duration=1800.0,
                file_path=f"{uuid.uuid4().hex}.mp3", asset_type="story_spacing",
            )
        )
    await db.commit()
    return station.id


async def _order(db: AsyncSession, station_id) -> list:
    rows = await db.execute(
        select(QueueEntry.asset_id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.source == "auto")
        .order_by(QueueEntry.position)
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_rounds_of_a_short_list_keep_each_story_far_from_itself(db_session: AsyncSession):
    station_id = await _stories_station(db_session)
    await QueueReplenishService(db_session, station_id).replenish()
    order = await _order(db_session, station_id)
    # 7 days of half-hour stories from 20: every story comes round many times
    assert len(order) > 300
    assert _closest_repeat(order) >= STORIES * 3 // 4 - 1, _closest_repeat(order)


@pytest.mark.asyncio
async def test_topping_up_after_each_story_does_not_pick_one_just_queued(db_session: AsyncSession):
    station_id = await _stories_station(db_session)
    await QueueReplenishService(db_session, station_id).replenish()
    for _ in range(40):
        first = (
            await db_session.execute(
                select(QueueEntry.id)
                .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
                .order_by(QueueEntry.position)
                .limit(1)
            )
        ).scalar_one()
        await db_session.execute(delete(QueueEntry).where(QueueEntry.id == first))
        await db_session.commit()
        await QueueReplenishService(db_session, station_id).replenish()
    order = await _order(db_session, station_id)
    assert _closest_repeat(order[-80:]) >= STORIES * 3 // 4 - 1, _closest_repeat(order[-80:])


@pytest.mark.asyncio
async def test_a_rule_filled_station_can_be_drawn_again(client: AsyncClient, db_session: AsyncSession):
    """The queue holds one story twice in a row (two copies were made one):
    Regenerate draws it again, also for a station with no playlists."""
    station_id = await _stories_station(db_session)
    ids = list((await db_session.execute(select(Asset.id).where(Asset.asset_type == "story_spacing"))).scalars().all())
    for position, asset_id in enumerate([ids[0], ids[1], ids[1], ids[2], ids[1]], start=1):
        db_session.add(
            QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=asset_id, position=position, status="pending")
        )
    await db_session.commit()
    _, h = await _headers(db_session, UserRole.MANAGER)
    r = await client.post(
        "/api/v1/playlists/generation/regenerate", params={"station_id": str(station_id)}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 4
    db_session.expire_all()
    order = await _order(db_session, station_id)
    assert len(order) > 300
    assert _closest_repeat(order) >= STORIES * 3 // 4 - 1, _closest_repeat(order)
