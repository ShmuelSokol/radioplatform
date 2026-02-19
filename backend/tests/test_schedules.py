import uuid
from datetime import time

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule import Schedule as ScheduleModel
from app.models.schedule_block import ScheduleBlock as ScheduleBlockModel
from app.models.station import Station


@pytest.mark.asyncio
async def test_create_schedule(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Schedule Test Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    response = await client.post(
        "/api/v1/schedules/",
        json={"name": "Morning Schedule", "station_id": str(station.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Morning Schedule"


@pytest.mark.asyncio
async def test_list_schedules(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="List Sched Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    schedule = ScheduleModel(id=uuid.uuid4(), station_id=station.id, name="Test Schedule")
    db_session.add(schedule)
    await db_session.commit()

    response = await client.get("/api/v1/schedules/")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_get_schedule(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Get Sched Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    schedule = ScheduleModel(id=uuid.uuid4(), station_id=station.id, name="Get Me Schedule")
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)

    response = await client.get(f"/api/v1/schedules/{schedule.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Me Schedule"


@pytest.mark.asyncio
async def test_delete_schedule(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Del Sched Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    schedule = ScheduleModel(id=uuid.uuid4(), station_id=station.id, name="Delete Schedule")
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)

    response = await client.delete(
        f"/api/v1/schedules/{schedule.id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_create_schedule_block(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Block Test Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    schedule = ScheduleModel(id=uuid.uuid4(), station_id=station.id, name="Block Schedule")
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)

    response = await client.post(
        "/api/v1/schedules/blocks",
        json={
            "schedule_id": str(schedule.id),
            "name": "Morning Block",
            "start_time": "08:00:00",
            "end_time": "12:00:00",
            "recurrence_type": "daily",
            "priority": 1,
            "playback_mode": "sequential",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Morning Block"
    assert data["playback_mode"] == "sequential"


@pytest.mark.asyncio
async def test_list_schedule_blocks(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="List Blocks Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    schedule = ScheduleModel(id=uuid.uuid4(), station_id=station.id, name="List Blocks Schedule")
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)

    block = ScheduleBlockModel(
        id=uuid.uuid4(),
        schedule_id=schedule.id,
        name="Block A",
        start_time=time(6, 0),
        end_time=time(10, 0),
        recurrence_type="daily",
    )
    db_session.add(block)
    await db_session.commit()

    response = await client.get(
        "/api/v1/schedules/blocks",
        params={"schedule_id": str(schedule.id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_delete_schedule_block(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Del Block Station")
    db_session.add(station)
    await db_session.commit()
    await db_session.refresh(station)

    schedule = ScheduleModel(id=uuid.uuid4(), station_id=station.id, name="Del Block Schedule")
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)

    block = ScheduleBlockModel(
        id=uuid.uuid4(),
        schedule_id=schedule.id,
        name="Delete Block",
        start_time=time(14, 0),
        end_time=time(18, 0),
        recurrence_type="daily",
    )
    db_session.add(block)
    await db_session.commit()
    await db_session.refresh(block)

    response = await client.delete(
        f"/api/v1/schedules/blocks/{block.id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_schedules_unauthorized(client: AsyncClient):
    response = await client.post(
        "/api/v1/schedules/",
        json={"name": "No Auth", "station_id": str(uuid.uuid4())},
    )
    assert response.status_code in (401, 403)
