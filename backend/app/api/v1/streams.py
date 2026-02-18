import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.playback_service import get_now_playing
from app.services.station_service import get_station

router = APIRouter(prefix="/stations", tags=["streams"])


@router.get("/{station_id}/stream")
async def stream_info(station_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    station = await get_station(db, station_id)
    return {
        "station_id": str(station.id),
        "station_name": station.name,
        "hls_url": f"/hls/{station_id}/main/live.m3u8",
    }


@router.get("/{station_id}/now-playing")
async def now_playing(station_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await get_station(db, station_id)  # validate station exists
    return await get_now_playing(str(station_id))
