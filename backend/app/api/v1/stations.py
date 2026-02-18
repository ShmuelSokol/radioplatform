import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_manager
from app.db.session import get_db
from app.models.user import User
from app.schemas.station import StationCreate, StationListResponse, StationResponse, StationUpdate
from app.services.station_service import (
    create_station,
    delete_station,
    get_station,
    list_stations,
    update_station,
)

router = APIRouter(prefix="/stations", tags=["stations"])


@router.post("", response_model=StationResponse, status_code=201)
async def create(
    body: StationCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    station = await create_station(db, body)
    return station


@router.get("", response_model=StationListResponse)
async def list_all(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    stations, total = await list_stations(db, skip=skip, limit=limit, active_only=active_only)
    return StationListResponse(stations=stations, total=total)


@router.get("/{station_id}", response_model=StationResponse)
async def get_one(station_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await get_station(db, station_id)


@router.put("/{station_id}", response_model=StationResponse)
async def update(
    station_id: uuid.UUID,
    body: StationUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    return await update_station(db, station_id, body)


@router.delete("/{station_id}", status_code=204)
async def delete(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await delete_station(db, station_id)
