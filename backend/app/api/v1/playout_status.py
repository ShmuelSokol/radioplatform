"""Where each station is playing from, for the badges in the control room, the
station list and the listener page (see wiki/streaming-architecture.md).

- "server": listeners hear the playout engine's stream; the engine is asked what it
  has on air and that is compared with the station's queue (in step or not).
- "browser": each listener's page plays the files itself (the built-in player).
Public: it says nothing secret, and the engine is asked at most every few seconds.
"""
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.services import playout_engine, playout_tunnel

router = APIRouter(prefix="/playout-status", tags=["playout"])

CACHE_SECONDS = 8.0
_cache: dict[str, tuple[float, dict]] = {}


async def station_status(db: AsyncSession, station_id: str) -> dict:
    if not settings.engine_drives(station_id):
        return {"mode": "browser"}
    hit = _cache.get(station_id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    engine = await playout_engine.engine_status(station_id)
    playing = (await db.execute(
        select(QueueEntry.id, Asset.title)
        .join(Asset, Asset.id == QueueEntry.asset_id)
        .where(QueueEntry.station_id == uuid.UUID(station_id), QueueEntry.status == "playing")
        .limit(1)
    )).first()
    in_sync = False
    if engine is not None and playing is not None:
        in_sync = (engine.get("entry_id") == str(playing.id)
                   or (bool(engine.get("title")) and engine.get("title") == playing.title))
    result = {
        # Listeners hear the stream only when it is switched on for them; otherwise the
        # engine drives the station but pages still play the files themselves
        "mode": "server" if settings.public_stream_url(station_id) else "browser",
        "engine": True,
        "engine_ok": engine is not None,
        "in_sync": in_sync,
        "engine_title": engine.get("title") if engine else None,
    }
    if station_id in settings.relay_station_ids:
        # A local FM box: what its relay last said about itself (None = not heard from)
        result["box"] = playout_tunnel.box_health(station_id)
    _cache[station_id] = (time.monotonic(), result)
    return result


@router.get("")
async def all_stations(db: AsyncSession = Depends(get_db)):
    """{station_id: status} for every engine station; any other station is "browser"."""
    out = {sid: await station_status(db, sid) for sid in sorted(settings.engine_station_ids)
           if settings.engine_drives(sid)}
    return {"stations": out, "default": {"mode": "browser"}}
