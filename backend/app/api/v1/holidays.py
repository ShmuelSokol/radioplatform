"""
Holiday/blackout window management endpoints.
"""
import asyncio
import logging
import tempfile
from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_db, require_manager
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.schemas.holiday import (
    AutoGenerateRequest,
    AutoGenerateResponse,
    HolidayWindowCreate,
    HolidayWindowInDB,
    HolidayWindowUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/holidays", tags=["holidays"])


@router.get("", response_model=list[HolidayWindowInDB])
async def list_holidays(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(HolidayWindow).offset(skip).limit(limit).order_by(HolidayWindow.start_datetime)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=HolidayWindowInDB, status_code=201)
async def create_holiday(
    data: HolidayWindowCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = HolidayWindow(**data.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.put("/{holiday_id}", response_model=HolidayWindowInDB)
async def update_holiday(
    holiday_id: UUID,
    data: HolidayWindowUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(HolidayWindow).where(HolidayWindow.id == holiday_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Holiday window not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{holiday_id}", status_code=204)
async def delete_holiday(
    holiday_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(HolidayWindow).where(HolidayWindow.id == holiday_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Holiday window not found")

    await db.delete(record)
    await db.commit()


@router.post("/auto-generate", response_model=AutoGenerateResponse)
async def auto_generate_blackouts(
    data: AutoGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Auto-generate Shabbos & Yom Tov blackout windows based on station location."""
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    # Look up station
    stmt = select(Station).where(Station.id == str(data.station_id))
    result = await db.execute(stmt)
    station = result.scalar_one_or_none()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    if not station.latitude or not station.longitude:
        raise HTTPException(
            status_code=400,
            detail="Station must have latitude and longitude set for auto-generation",
        )

    start_date = date.today()
    end_date = start_date + timedelta(days=data.months_ahead * 30)
    station_ids = [str(station.id)]

    # Generate windows
    shabbos = generate_shabbos_windows(
        station.latitude, station.longitude, station.timezone,
        start_date, end_date, station_ids,
    )
    yom_tov = generate_yom_tov_windows(
        station.latitude, station.longitude, station.timezone,
        start_date, end_date, station_ids,
    )
    all_windows = merge_overlapping_windows(shabbos + yom_tov)

    # Check for duplicates and bulk create
    created = 0
    skipped = 0

    for w in all_windows:
        w_start = datetime.fromisoformat(w["start_datetime"])
        # Skip if a window with same name exists and start is within 1 hour
        dup_stmt = select(HolidayWindow).where(
            HolidayWindow.name == w["name"],
            HolidayWindow.start_datetime >= w_start - timedelta(hours=1),
            HolidayWindow.start_datetime <= w_start + timedelta(hours=1),
        )
        dup_result = await db.execute(dup_stmt)
        if dup_result.scalar_one_or_none():
            skipped += 1
            continue

        record = HolidayWindow(
            name=w["name"],
            start_datetime=w_start,
            end_datetime=datetime.fromisoformat(w["end_datetime"]),
            is_blackout=w["is_blackout"],
            affected_stations=w.get("affected_stations"),
        )
        db.add(record)
        created += 1

    if created > 0:
        await db.commit()

    return AutoGenerateResponse(created=created, skipped=skipped)


@router.post("/ensure-silence-asset")
async def ensure_silence_asset(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Ensure a silence audio asset exists. Creates one via FFmpeg if needed."""
    # Check if silence asset already exists
    stmt = select(Asset).where(Asset.asset_type == "silence")
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        return {
            "id": str(existing.id),
            "title": existing.title,
            "file_path": existing.file_path,
            "already_existed": True,
        }

    # Generate 5-min silent MP3 via FFmpeg
    duration_seconds = 300  # 5 minutes
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    cmd = [
        settings.FFMPEG_PATH,
        "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration_seconds),
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        tmp_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg failed to generate silence: {stderr.decode()[:500]}",
        )

    # Read generated file
    with open(tmp_path, "rb") as f:
        file_data = f.read()

    # Upload to Supabase storage if available
    file_path = "system/silence_5min.mp3"
    if settings.supabase_storage_enabled:
        from app.services.supabase_storage_service import upload_to_supabase
        file_path = await upload_to_supabase(file_data, file_path)

    # Create Asset record
    asset = Asset(
        title="Silence (5 min)",
        artist=None,
        album=None,
        duration=float(duration_seconds),
        file_path=file_path,
        asset_type="silence",
        category="system",
        review_status="approved",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    # Clean up temp file
    import os
    try:
        os.unlink(tmp_path)
    except OSError:
        pass

    return {
        "id": str(asset.id),
        "title": asset.title,
        "file_path": asset.file_path,
        "already_existed": False,
    }
