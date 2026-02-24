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

    # Audio analysis data (defaults if missing)
    analysis = {}
    if asset.metadata_extra:
        analysis = asset.metadata_extra.get("audio_analysis", {})

    duration = asset.duration or 180.0
    cue_in = analysis.get("cue_in_seconds", 0)
    cue_out = analysis.get("cue_out_seconds", duration)
    cross_start = analysis.get("cross_start_seconds", duration - 3.0)
    replay_gain_db = analysis.get("replay_gain_db", 0)

    # Peek at next pending entry for pre-loading
    from sqlalchemy import or_
    now_utc = datetime.now(timezone.utc)
    next_result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now_utc),
        )
        .order_by(QueueEntry.position)
        .limit(1)
    )
    next_entry = next_result.scalar_one_or_none()

    next_asset_data = None
    if next_entry and next_entry.asset:
        na = next_entry.asset
        na_analysis = {}
        if na.metadata_extra:
            na_analysis = na.metadata_extra.get("audio_analysis", {})

        na_file_path = na.file_path
        na_audio_url = None
        if na_file_path.startswith("http://") or na_file_path.startswith("https://"):
            na_audio_url = na_file_path
        elif settings.supabase_storage_enabled:
            na_audio_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{na_file_path}"

        next_asset_data = {
            "id": str(na.id),
            "title": na.title,
            "artist": na.artist,
            "audio_url": na_audio_url,
            "cue_in": na_analysis.get("cue_in_seconds", 0),
            "replay_gain_db": na_analysis.get("replay_gain_db", 0),
        }

    response = {
        "playing": True,
        "asset_id": str(asset.id),
        "title": asset.title,
        "artist": asset.artist,
        "album": asset.album,
        "duration": duration,
        "elapsed": round(elapsed, 1),
        "started_at": entry.started_at.isoformat() if entry.started_at else None,
        "audio_url": audio_url,
        "cue_in": cue_in,
        "cue_out": cue_out,
        "cross_start": cross_start,
        "replay_gain_db": replay_gain_db,
        "next_asset": next_asset_data,
    }

    if settings.liquidsoap_enabled:
        response["hls_url"] = f"/hls/{station_id}/stream.m3u8"

    return response
