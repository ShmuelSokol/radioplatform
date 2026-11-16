"""Ready-made daily weather spots (e.g. the Shprach Kol B'ramah voice report).

The hourly scheduler plays one weather spot asset per day and only asks
ElevenLabs for one when that day has none. Uploading a recording here makes it
that day's spot; days with no upload keep the ElevenLabs fallback.
"""
import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager
from app.db.session import get_db
from app.models.asset import Asset
from app.models.user import User
from app.services.weather_spot_service import (
    find_time_clip_asset,
    find_weather_spot_asset,
    normalize_region,
    store_uploaded_time_clip,
    store_uploaded_weather_spot,
)

router = APIRouter(prefix="/weather-spots", tags=["weather-spots"])
time_router = APIRouter(prefix="/time-announcements", tags=["time-announcements"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


class WeatherSpotResponse(BaseModel):
    spot_date: date
    asset_id: str
    file_url: str
    duration: float | None
    source: str  # "shprach" (or another uploader), or "elevenlabs" for a generated spot
    script: str | None = None
    replaced: bool = False
    region: str = "lakewood"


def _to_response(spot_date: date, asset: Asset, replaced: bool = False) -> WeatherSpotResponse:
    extra = asset.metadata_extra or {}
    return WeatherSpotResponse(
        region=normalize_region(extra.get("region")),
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
    region: str | None = Query(None, description="Weather region, e.g. jerusalem (default: lakewood)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """The weather spot stations of this region will play on this date (404 if none exists yet)."""
    asset = await find_weather_spot_asset(db, spot_date.isoformat(), _region(region))
    if asset is None:
        raise HTTPException(404, "No weather spot for this date")
    return _to_response(spot_date, asset)


@router.put("/{spot_date}", response_model=WeatherSpotResponse)
async def upload_weather_spot(
    spot_date: date,
    file: UploadFile = File(...),
    script: str | None = Form(None),
    source: str = Form("shprach"),
    region: str | None = Query(None, description="Weather region, e.g. jerusalem (default: lakewood)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Upload a finished recording as the weather spot for a date (the region's local date, YYYY-MM-DD).

    Stations play the spot of their own region (automation_config.weather_region).
    A station with a region other than the default never falls back to another city's report.

    Replaces whatever spot that date already has, including one already queued.
    Any audio format FFmpeg reads is accepted; it is stored as loudness-normalized MP3.
    """
    data = await _read_upload(file, source)
    try:
        asset, replaced = await store_uploaded_weather_spot(
            db, spot_date.isoformat(), data, source=source, script=(script or None), region=_region(region)
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _to_response(spot_date, asset, replaced)


def _region(region: str | None) -> str:
    value = normalize_region(region)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", value):
        raise HTTPException(422, "region must be lowercase letters, digits or dashes")
    return value


async def _read_upload(file: UploadFile, source: str) -> bytes:
    if not re.fullmatch(r"[a-z0-9-]{1,30}", source):
        raise HTTPException(422, "source must be lowercase letters, digits or dashes")
    data = await file.read()
    if not data:
        raise HTTPException(422, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    return data


# ── Reusable hourly "the time is" clips ──────────────────────────────────────


class TimeClipResponse(BaseModel):
    hour: int  # 0-23, Eastern
    asset_id: str
    file_url: str
    duration: float | None
    source: str
    script: str | None = None
    replaced: bool = False


def _time_response(hour: int, asset: Asset, replaced: bool = False) -> TimeClipResponse:
    extra = asset.metadata_extra or {}
    return TimeClipResponse(
        hour=hour,
        asset_id=str(asset.id),
        file_url=asset.file_path,
        duration=asset.duration,
        source=extra.get("source") or "unknown",
        script=extra.get("script"),
        replaced=replaced,
    )


@time_router.get("", response_model=list[TimeClipResponse])
async def list_time_clips(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """The uploaded clips, by hour. Hours not listed fall back to ElevenLabs (if configured)."""
    clips = []
    for hour in range(24):
        asset = await find_time_clip_asset(db, hour)
        if asset is not None:
            clips.append(_time_response(hour, asset))
    return clips


@time_router.put("/{hour}", response_model=TimeClipResponse)
async def upload_time_clip(
    hour: int,
    file: UploadFile = File(...),
    script: str | None = Form(None),
    source: str = Form("shprach"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Upload the recording played at the top of this hour (0-23, Eastern) every day."""
    if not 0 <= hour <= 23:
        raise HTTPException(422, "hour must be 0-23")
    data = await _read_upload(file, source)
    try:
        asset, replaced = await store_uploaded_time_clip(db, hour, data, source=source, script=(script or None))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _time_response(hour, asset, replaced)
