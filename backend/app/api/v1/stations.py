import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import may_operate_station, require_manager
from app.core.exceptions import ForbiddenError
from app.models.user import UserRole
from app.services.station_service import get_station
from app.db.session import get_db
from app.models.user import User
from app.schemas.station import StationCreate, StationListResponse, StationResponse, StationUpdate
from app.services.station_service import (
    create_station,
    delete_station,
    list_stations,
    update_station,
)

logger = logging.getLogger(__name__)

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


@router.get("/geocode")
async def geocode_city(
    q: str = Query(..., min_length=2, max_length=100),
    _user: User = Depends(require_manager),
):
    """Search for cities by name using OpenStreetMap Nominatim. Returns up to 5 results."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": q,
                    "format": "json",
                    "limit": 5,
                    "addressdetails": 1,
                    "featuretype": "city",
                },
                headers={"User-Agent": "KolBramahRadio/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Geocode request failed: %s", e)
        return []

    from app.services.timezone_service import get_timezone_for_coords

    results = []
    for item in data:
        addr = item.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or item.get("name", "")
        state = addr.get("state", "")
        country = addr.get("country", "")
        display = ", ".join(part for part in [city, state, country] if part)
        lat = float(item["lat"])
        lon = float(item["lon"])
        results.append({
            "display_name": display,
            "latitude": lat,
            "longitude": lon,
            "timezone": get_timezone_for_coords(lat, lon),
        })
    return results


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
    station = await get_station(db, station_id)
    if not may_operate_station(_user, station):
        raise ForbiddenError("Your login is not set up to operate this station")
    if body.protected is not None and body.protected != station.protected and _user.role != UserRole.ADMIN:
        raise ForbiddenError("Only an admin can protect or unprotect a station")
    if body.protected is None:
        body.model_fields_set.discard("protected")
    updated = await update_station(db, station_id, body)
    # The song crossfade length takes effect at once (queue.handover_seconds)
    from app.api.v1.queue import note_station_crossfade

    note_station_crossfade(updated)
    return updated


@router.delete("/{station_id}", status_code=204)
async def delete(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    station = await get_station(db, station_id)
    if station.protected and _user.role != UserRole.ADMIN:
        raise ForbiddenError("Only an admin can delete a protected station")
    if not may_operate_station(_user, station):
        raise ForbiddenError("Your login is not set up to operate this station")
    await delete_station(db, station_id)
