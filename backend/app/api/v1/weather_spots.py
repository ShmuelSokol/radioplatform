"""Ready-made daily weather spots (e.g. the Shprach Kol Berama voice report).

The hourly scheduler plays one weather spot asset per day and only asks
ElevenLabs for one when that day has none. Uploading a recording here makes it
that day's spot; days with no upload keep the ElevenLabs fallback.
"""
import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager
from app.db.session import get_db
from app.models.asset import Asset
from app.models.user import User
from app.services.weather_spot_service import find_weather_spot_asset, store_uploaded_weather_spot

router = APIRouter(prefix="/weather-spots", tags=["weather-spots"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


class WeatherSpotResponse(BaseModel):
    spot_date: date
    asset_id: str
    file_url: str
    duration: float | None
    source: str  # "shprach" (or another uploader), or "elevenlabs" for a generated spot
    script: str | None = None
    replaced: bool = False


def _to_response(spot_date: date, asset: Asset, replaced: bool = False) -> WeatherSpotResponse:
    extra = asset.metadata_extra or {}
    return WeatherSpotResponse(
        spot_date=spot_date,
        asset_id=str(asset.id),
        file_url=asset.file_path,
        duration=asset.duration,
        source=extra.get("source") or ("elevenlabs" if extra.get("generated") else "unknown"),
        script=extra.get("script"),
        replaced=replaced,
    )


@router.get("/{spot_date}", response_model=WeatherSpotResponse)
async def get_weather_spot(
    spot_date: date,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """The weather spot the station will play on this date (404 if none exists yet)."""
    asset = await find_weather_spot_asset(db, spot_date.isoformat())
    if asset is None:
        raise HTTPException(404, "No weather spot for this date")
    return _to_response(spot_date, asset)


@router.put("/{spot_date}", response_model=WeatherSpotResponse)
async def upload_weather_spot(
    spot_date: date,
    file: UploadFile = File(...),
    script: str | None = Form(None),
    source: str = Form("shprach"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Upload a finished recording as the weather spot for a date (Eastern, YYYY-MM-DD).

    Replaces whatever spot that date already has, including one already queued.
    Any audio format FFmpeg reads is accepted; it is stored as loudness-normalized MP3.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,30}", source):
        raise HTTPException(422, "source must be lowercase letters, digits or dashes")
    data = await file.read()
    if not data:
        raise HTTPException(422, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    try:
        asset, replaced = await store_uploaded_weather_spot(
            db, spot_date.isoformat(), data, source=source, script=(script or None)
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _to_response(spot_date, asset, replaced)
