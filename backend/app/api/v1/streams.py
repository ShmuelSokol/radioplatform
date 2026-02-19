import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.queue_entry import QueueEntry
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


@router.get("/{station_id}/live-audio")
async def live_audio(station_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Public endpoint: returns audio URL + timing for the currently playing track.

    No authentication required — used by the public listener page.
    """
    from app.config import settings

    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
        .limit(1)
    )
    entry = result.scalar_one_or_none()

    if not entry or not entry.asset:
        return {"playing": False}

    asset = entry.asset
    file_path = asset.file_path

    # Build audio URL
    audio_url = None
    if file_path.startswith("http://") or file_path.startswith("https://"):
        audio_url = file_path
    elif settings.supabase_storage_enabled:
        bucket = settings.SUPABASE_STORAGE_BUCKET
        audio_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_path}"

    # Calculate elapsed
    elapsed = 0.0
    if entry.started_at:
        elapsed = (datetime.now(timezone.utc) - entry.started_at).total_seconds()

    return {
        "playing": True,
        "asset_id": str(asset.id),
        "title": asset.title,
        "artist": asset.artist,
        "album": asset.album,
        "duration": asset.duration,
        "elapsed": round(elapsed, 1),
        "started_at": entry.started_at.isoformat() if entry.started_at else None,
        "audio_url": audio_url,
    }
