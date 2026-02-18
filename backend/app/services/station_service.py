import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.station import Station
from app.schemas.station import StationCreate, StationUpdate


async def create_station(db: AsyncSession, data: StationCreate) -> Station:
    existing = await db.execute(select(Station).where(Station.name == data.name))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Station '{data.name}' already exists")

    station = Station(**data.model_dump())
    db.add(station)
    await db.flush()
    await db.refresh(station)
    return station


async def get_station(db: AsyncSession, station_id: uuid.UUID) -> Station:
    result = await db.execute(
        select(Station).options(selectinload(Station.channels)).where(Station.id == station_id)
    )
    station = result.scalar_one_or_none()
    if not station:
        raise NotFoundError(f"Station {station_id} not found")
    return station


async def list_stations(
    db: AsyncSession, skip: int = 0, limit: int = 50, active_only: bool = False
) -> tuple[list[Station], int]:
    query = select(Station).options(selectinload(Station.channels))
    count_query = select(func.count(Station.id))

    if active_only:
        query = query.where(Station.is_active == True)  # noqa: E712
        count_query = count_query.where(Station.is_active == True)  # noqa: E712

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.offset(skip).limit(limit).order_by(Station.name))
    stations = list(result.scalars().all())
    return stations, total


async def update_station(db: AsyncSession, station_id: uuid.UUID, data: StationUpdate) -> Station:
    station = await get_station(db, station_id)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(station, field, value)
    await db.flush()
    await db.refresh(station)
    return station


async def delete_station(db: AsyncSession, station_id: uuid.UUID) -> None:
    station = await get_station(db, station_id)
    await db.delete(station)
    await db.flush()
