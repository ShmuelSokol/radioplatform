"""
Holiday/blackout window management endpoints.
"""
import asyncio
import logging
import tempfile
from datetime import date, datetime, timedelta, timezone
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
    HolidayListResponse,
    HolidayWindowCreate,
    HolidayWindowInDB,
    HolidayWindowUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/holidays", tags=["holidays"])


@router.get("", response_model=HolidayListResponse)
async def list_holidays(
    skip: int = 0,
    limit: int = 200,
    reason: str | None = None,
    status: str | None = None,
    station_id: str | None = None,
    start_after: str | None = None,
    start_before: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    from sqlalchemy import func

    limit = min(limit, 500)
    stmt = select(HolidayWindow)
    count_stmt = select(func.count(HolidayWindow.id))

    # Filter by reason
    if reason:
        stmt = stmt.where(HolidayWindow.reason == reason)
        count_stmt = count_stmt.where(HolidayWindow.reason == reason)

    # Filter by status (upcoming/active/active_upcoming/past)
    now = datetime.now(timezone.utc)
    if status == "active_upcoming":
        stmt = stmt.where(HolidayWindow.end_datetime > now)
        count_stmt = count_stmt.where(HolidayWindow.end_datetime > now)
    elif status == "upcoming":
        stmt = stmt.where(HolidayWindow.start_datetime > now)
        count_stmt = count_stmt.where(HolidayWindow.start_datetime > now)
    elif status == "active":
        stmt = stmt.where(HolidayWindow.start_datetime <= now, HolidayWindow.end_datetime > now)
        count_stmt = count_stmt.where(HolidayWindow.start_datetime <= now, HolidayWindow.end_datetime > now)
    elif status == "past":
        stmt = stmt.where(HolidayWindow.end_datetime <= now)
        count_stmt = count_stmt.where(HolidayWindow.end_datetime <= now)

    # Filter by station_id (check JSONB or NULL = all stations)
    if station_id:
        from sqlalchemy import or_, cast, String
        from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
        stmt = stmt.where(
            or_(
                HolidayWindow.affected_stations.is_(None),
                HolidayWindow.affected_stations["station_ids"].astext.contains(station_id),
            )
        )
        count_stmt = count_stmt.where(
            or_(
                HolidayWindow.affected_stations.is_(None),
                HolidayWindow.affected_stations["station_ids"].astext.contains(station_id),
            )
        )

    # Filter by date range
    if start_after:
        start_after_dt = datetime.fromisoformat(start_after)
        stmt = stmt.where(HolidayWindow.start_datetime >= start_after_dt)
        count_stmt = count_stmt.where(HolidayWindow.start_datetime >= start_after_dt)
    if start_before:
        start_before_dt = datetime.fromisoformat(start_before)
        stmt = stmt.where(HolidayWindow.start_datetime <= start_before_dt)
        count_stmt = count_stmt.where(HolidayWindow.start_datetime <= start_before_dt)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(HolidayWindow.start_datetime).offset(skip).limit(limit)
    result = await db.execute(stmt)
    holidays = result.scalars().all()

    return HolidayListResponse(holidays=holidays, total=total)


def _infer_reason(name: str) -> str:
    """Infer reason from holiday name."""
    lower = name.lower()
    if "yom kippur" in lower:
        return "Yom Kippur"
    if "rosh hashanah" in lower:
        return "Rosh Hashanah"
    if "sukkot" in lower:
        return "Sukkot"
    if "shemini" in lower or "simchat" in lower:
        return "Shemini Atzeret"
    if "pesach" in lower:
        return "Pesach"
    if "shavuot" in lower:
        return "Shavuot"
    if "shabbos" in lower or "shabbat" in lower:
        return "Shabbos"
    return "Manual"


@router.post("", response_model=HolidayWindowInDB, status_code=201)
async def create_holiday(
    data: HolidayWindowCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    dump = data.model_dump()
    if not dump.get("reason"):
        dump["reason"] = _infer_reason(dump["name"])
    record = HolidayWindow(**dump)
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # If this blackout is active now or starting soon, fill affected stations with silence
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    rec_start = record.start_datetime.replace(tzinfo=_tz.utc) if record.start_datetime.tzinfo is None else record.start_datetime
    rec_end = record.end_datetime.replace(tzinfo=_tz.utc) if record.end_datetime.tzinfo is None else record.end_datetime
    if record.is_blackout and rec_end > now:
        try:
            from app.api.v1.queue import fill_blackout_queue
            affected = record.affected_stations
            if affected is None:
                station_result = await db.execute(select(Station).where(Station.is_active == True))
                for st in station_result.scalars().all():
                    await fill_blackout_queue(db, st.id, record)
            else:
                for sid in affected.get("station_ids", []):
                    await fill_blackout_queue(db, sid, record)
        except Exception as e:
            logger.warning("Failed to fill blackout queue on holiday create: %s", e)

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


@router.post("/preview")
async def preview_blackouts(
    data: AutoGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Preview auto-generated blackout windows without saving to DB."""
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

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

    start_date = date.today() - timedelta(days=7)
    end_date = date.today() + timedelta(days=data.months_ahead * 30)
    station_ids = [str(station.id)]

    shabbos = generate_shabbos_windows(
        station.latitude, station.longitude, station.timezone,
        start_date, end_date, station_ids,
    )
    yom_tov = generate_yom_tov_windows(
        station.latitude, station.longitude, station.timezone,
        start_date, end_date, station_ids,
    )
    all_windows = merge_overlapping_windows(shabbos + yom_tov)

    shabbos_count = sum(1 for w in all_windows if "shabbos" in w["name"].lower() or "shabbat" in w["name"].lower())
    yom_tov_count = len(all_windows) - shabbos_count

    windows = []
    for w in all_windows:
        start_dt = datetime.fromisoformat(w["start_datetime"])
        end_dt = datetime.fromisoformat(w["end_datetime"])
        duration_hours = round((end_dt - start_dt).total_seconds() / 3600, 2)
        windows.append({
            "name": w["name"],
            "start_datetime": w["start_datetime"],
            "end_datetime": w["end_datetime"],
            "duration_hours": duration_hours,
        })

    return {
        "total": len(all_windows),
        "shabbos_count": shabbos_count,
        "yom_tov_count": yom_tov_count,
        "windows": windows,
    }


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

    start_date = date.today() - timedelta(days=7)
    end_date = date.today() + timedelta(days=data.months_ahead * 30)
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
            reason=w.get("reason") or _infer_reason(w["name"]),
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
