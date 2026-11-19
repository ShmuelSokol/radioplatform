import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.schemas.station import StationCreate, StationUpdate

logger = logging.getLogger(__name__)


def window_covers_station(window: HolidayWindow, station_id) -> bool:
    """A blackout with no station list covers every station."""
    affected = window.affected_stations
    if affected is None:
        return True
    ids = affected.get("station_ids") if isinstance(affected, dict) else None
    if ids is None:
        return True
    return str(station_id) in [str(i) for i in ids]


# A stored window further than this from the computed times is corrected
GENERATED_TOLERANCE = timedelta(seconds=60)
# A stored window of the same name this close to the computed start is the same Shabbos
# or Yom Tov (the next one of the same name is days away)
SAME_WINDOW = timedelta(hours=3)
# created_at and updated_at this close together: nobody edited the row since it was made
UNTOUCHED = timedelta(seconds=5)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _apart(stored: datetime, computed: datetime) -> timedelta:
    """How far a stored time is from a computed one. Postgres gives the stored time with
    its zone; SQLite (the tests) gives back the clock time the generator wrote, with no
    zone, so it is compared with the computed clock time."""
    if stored.tzinfo is None:
        return abs(stored - computed.replace(tzinfo=None))
    return abs(stored - computed)


def _only_station(window: HolidayWindow, station_id) -> bool:
    affected = window.affected_stations
    ids = affected.get("station_ids") if isinstance(affected, dict) else None
    return ids is not None and [str(i) for i in ids] == [str(station_id)]


async def store_generated_windows(db: AsyncSession, station: Station, windows: list[dict],
                                  default_reason=None) -> tuple[int, int, int]:
    """Save computed Shabbos and Yom Tov windows for one station: (created, updated, skipped).

    A window already stored for this station (same name, start within 3 hours) is not
    made twice. When its times differ from the computed ones (the candle-lighting rule
    changed: Jerusalem went from 18 to 40 minutes on 2026-11-19 and 58 stored windows
    kept the late start) it is corrected, but only when it is safe to say the generator
    owns it: it has not started yet, it belongs to this station alone, and nobody has
    edited it since it was made. Anything else is left as it is.

    A window of another station does not count as the same one: matching on name and
    time alone left a second station in the same area with no Shabbos at all.
    """
    now = datetime.now(UTC)
    created = updated = skipped = 0
    for w in windows:
        w_start = datetime.fromisoformat(w["start_datetime"])
        w_end = datetime.fromisoformat(w["end_datetime"])
        found = (await db.execute(select(HolidayWindow).where(
            HolidayWindow.name == w["name"],
            HolidayWindow.start_datetime >= w_start - SAME_WINDOW,
            HolidayWindow.start_datetime <= w_start + SAME_WINDOW,
        ))).scalars().all()
        mine = [d for d in found if window_covers_station(d, station.id)]
        if not mine:
            reason = w.get("reason") or (default_reason(w["name"]) if callable(default_reason) else default_reason)
            db.add(HolidayWindow(
                name=w["name"], start_datetime=w_start, end_datetime=w_end,
                is_blackout=w["is_blackout"], affected_stations=w.get("affected_stations"),
                reason=reason or "Manual",
            ))
            created += 1
            continue
        skipped += 1
        if len(mine) != 1:
            continue
        old = mine[0]
        off = max(_apart(old.start_datetime, w_start), _apart(old.end_datetime, w_end))
        if off <= GENERATED_TOLERANCE:
            continue
        untouched = abs(_aware(old.updated_at) - _aware(old.created_at)) <= UNTOUCHED
        began = w_start <= now or _apart(old.start_datetime, now) == timedelta(0) or (
            old.start_datetime.tzinfo is not None and old.start_datetime <= now)
        if began or not _only_station(old, station.id) or not untouched:
            continue
        logger.info("Blackout window %r of station %s corrected: start %s -> %s, end %s -> %s",
                    old.name, station.id, old.start_datetime, w_start, old.end_datetime, w_end)
        old.start_datetime, old.end_datetime = w_start, w_end
        # still the generator's own row: the next run may correct it again
        old.created_at = old.updated_at = now
        skipped -= 1
        updated += 1
    return created, updated, skipped


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

    created, updated, _skipped = await store_generated_windows(db, station, all_windows)

    if created or updated:
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


def _same_start(stored: datetime, made: datetime) -> bool:
    """Stored start equals a generated one (to the minute). SQLite drops the zone of stored
    rows and keeps the wall time, so a naive stored value is compared as wall time."""
    if stored.tzinfo is None:
        made = made.replace(tzinfo=None)
    return abs((stored - made).total_seconds()) < 60


async def drop_generated_holidays_for_location(
    db: AsyncSession, station: Station, lat: float, lon: float, tz_name: str | None,
) -> int:
    """Delete the Shabbos / Yom Tov windows that were generated for this station at the given
    (old) location: windows covering this station only, whose name and start match what the
    generator makes for that place. Hand-made windows, windows shared with other stations,
    and a window in progress (it ends at its own time) are left alone. Returns the count."""
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    rows = (await db.execute(select(HolidayWindow))).scalars().all()
    own = [w for w in rows
           if isinstance(w.affected_stations, dict)
           and [str(i) for i in w.affected_stations.get("station_ids") or []] == [str(station.id)]]
    if not own:
        return 0
    # Regenerate through the END of the last window: a Yom Tov that runs into Shabbos is
    # stored merged ("Pesach (First Days) / Shabbos"), and stopping at its start day made
    # only "Pesach (First Days)", so the old place's merged window was never matched and
    # stayed after the move (found running the suite with the clock in March).
    last = max(w.end_datetime for w in own).date()
    start_date = date.today() - timedelta(days=7)
    end_date = max(last, start_date) + timedelta(days=2)
    ids = [str(station.id)]
    made = merge_overlapping_windows(
        generate_shabbos_windows(lat, lon, tz_name, start_date, end_date, ids)
        + generate_yom_tov_windows(lat, lon, tz_name, start_date, end_date, ids)
    )
    by_name: dict[str, list[datetime]] = {}
    for m in made:
        by_name.setdefault(m["name"], []).append(datetime.fromisoformat(m["start_datetime"]))

    now = datetime.now(UTC)

    def aware(t: datetime) -> datetime:
        return t if t.tzinfo else t.replace(tzinfo=UTC)

    dropped = 0
    for w in own:
        if not any(_same_start(w.start_datetime, t) for t in by_name.get(w.name, [])):
            continue                                    # hand-made, or not this place's
        if aware(w.start_datetime) <= now < aware(w.end_datetime):
            continue                                    # on air now: let it end
        await db.delete(w)
        dropped += 1
    if dropped:
        await db.flush()
    return dropped


async def create_station(db: AsyncSession, data: StationCreate) -> Station:
    existing = await db.execute(select(Station).where(Station.name == data.name))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Station '{data.name}' already exists")

    station_data = data.model_dump()
    # Auto-derive timezone from coordinates, unless the request names a time zone itself
    # ("UTC", the default, counts as not chosen)
    chose_tz = "timezone" in data.model_fields_set and data.timezone not in (None, "", "UTC")
    if data.latitude is not None and data.longitude is not None and not chose_tz:
        from app.services.timezone_service import get_timezone_for_coords
        station_data["timezone"] = get_timezone_for_coords(data.latitude, data.longitude)

    station = Station(**station_data)
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

    # Re-derive timezone when lat/lon changes
    new_lat = update_data.get("latitude", station.latitude)
    new_lon = update_data.get("longitude", station.longitude)
    # A time zone sent in the same request is the user's choice and is kept ("UTC" is not one)
    chose_tz = update_data.get("timezone") not in (None, "", "UTC")
    if ("latitude" in update_data or "longitude" in update_data) and new_lat is not None and new_lon is not None \
            and not chose_tz:
        from app.services.timezone_service import get_timezone_for_coords
        update_data["timezone"] = get_timezone_for_coords(new_lat, new_lon)

    old_place = (station.latitude, station.longitude, station.timezone)
    for field, value in update_data.items():
        setattr(station, field, value)
    await db.flush()
    await db.refresh(station)

    # Moved: the blackouts made for the old place go, the new place's are made now
    # (not at the next daily extension), so Shabbos follows the station
    if (station.latitude, station.longitude) != old_place[:2] and station.latitude and station.longitude:
        try:
            dropped = 0
            if old_place[0] and old_place[1]:
                dropped = await drop_generated_holidays_for_location(db, station, *old_place)
            made = await auto_generate_holidays_for_station(db, station)
            logger.info("Station %s moved: %d old blackout windows removed, %d made", station.name, dropped, made)
        except Exception as e:
            logger.warning("Blackout regeneration after moving station %s failed: %s", station.name, e)
    return station


async def delete_station(db: AsyncSession, station_id: uuid.UUID) -> None:
    station = await get_station(db, station_id)
    await db.delete(station)
    await db.flush()
