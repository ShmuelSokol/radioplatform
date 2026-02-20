import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.schemas.station import StationCreate, StationUpdate

logger = logging.getLogger(__name__)


async def auto_generate_holidays_for_station(db: AsyncSession, station: Station, months_ahead: int = 13) -> int:
    """Generate Shabbos + Yom Tov blackout windows for a station. Returns count of created windows."""
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    if not station.latitude or not station.longitude:
        return 0

    # Use 7-day lookback to ensure current-week Shabbat is always included
    # (e.g., if regeneration runs on Saturday, this Friday's window still gets created)
    start_date = date.today() - timedelta(days=7)
    end_date = date.today() + timedelta(days=months_ahead * 30)
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

    created = 0
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
            continue

        record = HolidayWindow(
            name=w["name"],
            start_datetime=w_start,
            end_datetime=datetime.fromisoformat(w["end_datetime"]),
            is_blackout=w["is_blackout"],
            affected_stations=w.get("affected_stations"),
            reason=w.get("reason", "Manual"),
        )
        db.add(record)
        created += 1

    if created > 0:
        await db.flush()

    # Ensure silence asset exists
    try:
        from app.models.asset import Asset
        stmt = select(Asset).where(Asset.asset_type == "silence").limit(1)
        result = await db.execute(stmt)
        if not result.scalar_one_or_none():
            import asyncio
            import tempfile
            from app.config import settings
            duration_seconds = 300
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
            cmd = [
                settings.FFMPEG_PATH, "-y", "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(duration_seconds), "-c:a", "libmp3lame", "-b:a", "128k",
                tmp_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                file_path = "system/silence_5min.mp3"
                if settings.supabase_storage_enabled:
                    from app.services.supabase_storage_service import upload_to_supabase
                    with open(tmp_path, "rb") as f:
                        file_data = f.read()
                    file_path = await upload_to_supabase(file_data, file_path)
                asset = Asset(
                    title="Silence (5 min)", duration=float(duration_seconds),
                    file_path=file_path, asset_type="silence",
                    category="system", review_status="approved",
                )
                db.add(asset)
                await db.flush()
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as e:
        logger.warning("Could not ensure silence asset: %s", e)

    logger.info("Auto-generated %d holiday windows for station %s", created, station.name)
    return created


async def create_station(db: AsyncSession, data: StationCreate) -> Station:
    existing = await db.execute(select(Station).where(Station.name == data.name))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Station '{data.name}' already exists")

    station = Station(**data.model_dump())
    db.add(station)
    await db.flush()
    await db.refresh(station)

    # Auto-generate holidays if station has location
    if station.latitude and station.longitude:
        try:
            count = await auto_generate_holidays_for_station(db, station)
            if count > 0:
                logger.info("Auto-generated %d holiday windows for new station %s", count, station.name)
        except Exception as e:
            logger.warning("Holiday auto-generation failed for station %s: %s", station.name, e)

    return station


async def get_station(db: AsyncSession, station_id: uuid.UUID) -> Station:
    result = await db.execute(
        select(Station).options(selectinload(Station.channels)).where(Station.id == station_id)
    )
    station = result.scalar_one_or_none()
    if not station:
        raise NotFoundError(f"Station {station_id} not found")
    return station


async def list_stations(
    db: AsyncSession, skip: int = 0, limit: int = 50, active_only: bool = False
) -> tuple[list[Station], int]:
    query = select(Station).options(selectinload(Station.channels))
    count_query = select(func.count(Station.id))

    if active_only:
        query = query.where(Station.is_active == True)  # noqa: E712
        count_query = count_query.where(Station.is_active == True)  # noqa: E712

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(query.offset(skip).limit(limit).order_by(Station.name))
    stations = list(result.scalars().all())
    return stations, total


async def update_station(db: AsyncSession, station_id: uuid.UUID, data: StationUpdate) -> Station:
    station = await get_station(db, station_id)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(station, field, value)
    await db.flush()
    await db.refresh(station)
    return station


async def delete_station(db: AsyncSession, station_id: uuid.UUID) -> None:
    station = await get_station(db, station_id)
    await db.delete(station)
    await db.flush()
