"""Old station controls. Play / pause / stop / enqueue drive the old Redis list player;
without Redis (production has none) they answer 409 instead of failing. Play-now puts
the asset on air through the queue, the station's one player."""
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_dj_or_manager, require_station_operator
from app.core.exceptions import ConflictError
from app.db.session import get_db
from app.models.user import User
from app.services.asset_service import get_asset
from app.schemas.queue import QueueFireNow
from app.services.playback_service import pause_playback, start_playback, stop_current
from app.services.station_service import get_station
from app.streaming.playlist_engine import PlaylistEngine

logger = logging.getLogger(__name__)

NO_REDIS = "These old controls need Redis, which is not set up. Use the control room (the queue)."


async def _redis_call(coro):
    """Run a Redis-backed control; no Redis is a 409 with a plain reason, not a 500.
    With Redis up, the old player also calls its stream server over HTTP: that being
    unreachable is the same "not set up" answer (CI has Redis but no stream server)."""
    try:
        return await coro
    except (RedisError, OSError, ValueError, httpx.HTTPError) as exc:
        logger.warning("Old controls without Redis: %s", exc)
        raise ConflictError(NO_REDIS) from exc


router = APIRouter(
    prefix="/stations", tags=["controls"], dependencies=[Depends(require_station_operator)]
)


class EnqueueRequest(BaseModel):
    asset_id: str


class PlayNowRequest(BaseModel):
    asset_id: str


@router.post("/{station_id}/controls/play")
async def play(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    await get_station(db, station_id)
    return await _redis_call(start_playback(str(station_id)))


@router.post("/{station_id}/controls/pause")
async def pause(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    await get_station(db, station_id)
    return await _redis_call(pause_playback(str(station_id)))


@router.post("/{station_id}/controls/stop")
async def stop(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    await get_station(db, station_id)
    await _redis_call(stop_current(str(station_id)))
    return {"status": "stopped"}


@router.post("/{station_id}/controls/play-now")
async def play_now_endpoint(
    station_id: uuid.UUID,
    body: PlayNowRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Put the asset on air now through the queue (same as the control room's fire-now).
    It used to start an HLS player beside the queue: two players on one station."""
    from app.api.v1.queue import fire_now

    await get_station(db, station_id)
    return await fire_now(station_id, QueueFireNow(asset_id=uuid.UUID(body.asset_id)), db, _user)


@router.post("/{station_id}/controls/enqueue")
async def enqueue(
    station_id: uuid.UUID,
    body: EnqueueRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    await get_station(db, station_id)
    asset = await get_asset(db, uuid.UUID(body.asset_id))
    engine = PlaylistEngine(str(station_id))

    async def _enqueue():
        await engine.enqueue(str(asset.id), asset.title, asset.file_path, asset.duration or 0)
        return await engine.queue_length()

    length = await _redis_call(_enqueue())
    return {"status": "enqueued", "queue_length": length}
