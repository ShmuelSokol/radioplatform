import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager
from app.db.session import get_db
from app.models.user import User
from app.services.asset_service import get_asset
from app.services.playback_service import pause_playback, play_now, start_playback, stop_current
from app.services.station_service import get_station
from app.streaming.playlist_engine import PlaylistEngine

router = APIRouter(prefix="/stations", tags=["controls"])


class EnqueueRequest(BaseModel):
    asset_id: str


class PlayNowRequest(BaseModel):
    asset_id: str


@router.post("/{station_id}/controls/play")
async def play(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await get_station(db, station_id)
    return await start_playback(str(station_id))


@router.post("/{station_id}/controls/pause")
async def pause(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await get_station(db, station_id)
    return await pause_playback(str(station_id))


@router.post("/{station_id}/controls/stop")
async def stop(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await get_station(db, station_id)
    await stop_current(str(station_id))
    return {"status": "stopped"}


@router.post("/{station_id}/controls/play-now")
async def play_now_endpoint(
    station_id: uuid.UUID,
    body: PlayNowRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await get_station(db, station_id)
    asset = await get_asset(db, uuid.UUID(body.asset_id))
    return await play_now(
        str(station_id),
        str(asset.id),
        asset.title,
        asset.file_path,
        asset.duration or 0,
    )


@router.post("/{station_id}/controls/enqueue")
async def enqueue(
    station_id: uuid.UUID,
    body: EnqueueRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await get_station(db, station_id)
    asset = await get_asset(db, uuid.UUID(body.asset_id))
    engine = PlaylistEngine(str(station_id))
    await engine.enqueue(str(asset.id), asset.title, asset.file_path, asset.duration or 0)
    length = await engine.queue_length()
    return {"status": "enqueued", "queue_length": length}
