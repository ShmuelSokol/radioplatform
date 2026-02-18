import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.asset import Asset
from app.services.supabase_storage_service import upload_to_supabase
from app.services.tts_service import generate_tts
from app.services.weather_service import get_current_weather

logger = logging.getLogger(__name__)


def _utc_to_eastern(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to US/Eastern (handles EST/EDT)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    return utc_dt.astimezone(ZoneInfo("America/New_York"))


def _build_time_text(eastern_now: datetime) -> str:
    hour = eastern_now.strftime("%I").lstrip("0")
    minute = eastern_now.strftime("%M")
    ampm = eastern_now.strftime("%p")
    return f"The time is {hour}:{minute} {ampm} on Kol Bramah."


def _build_weather_text(weather: dict) -> str:
    return (
        f"Currently in Lakewood: {weather['temp_f']} degrees and {weather['description']}, "
        f"winds from the {weather['wind_direction']} at {weather['wind_speed_mph']} miles per hour."
    )


async def get_or_create_weather_spot_assets(
    db: AsyncSession, slot_key: str
) -> tuple[Asset | None, Asset | None]:
    """Get or create time-announcement and weather-spot assets for a 15-min slot.

    Args:
        db: Database session.
        slot_key: Dedup key like "2024-01-15T15:30".

    Returns:
        (time_asset, weather_asset) — either may be None on failure.
    """
    # Check if assets already exist for this slot
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "jingle",
            Asset.category == "time_announcement",
            Asset.metadata_extra["slot_key"].astext == slot_key,
        ).limit(1)
    )
    existing_time = result.scalar_one_or_none()

    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "spot",
            Asset.category == "weather_spot",
            Asset.metadata_extra["slot_key"].astext == slot_key,
        ).limit(1)
    )
    existing_weather = result.scalar_one_or_none()

    if existing_time and existing_weather:
        return existing_time, existing_weather

    now_utc = datetime.now(timezone.utc)
    eastern_now = _utc_to_eastern(now_utc)
    safe_slot = slot_key.replace(":", "-")

    # Generate time announcement if needed
    time_asset = existing_time
    if not time_asset:
        try:
            time_text = _build_time_text(eastern_now)
            time_bytes, time_duration = await generate_tts(time_text)
            time_path = f"weather/{safe_slot}_time.mp3"
            time_url = await upload_to_supabase(time_bytes, time_path)

            time_asset = Asset(
                id=uuid.uuid4(),
                title=f"Time Announcement - {slot_key}",
                artist="Kol Bramah",
                duration=time_duration,
                file_path=time_url,
                asset_type="jingle",
                category="time_announcement",
                metadata_extra={"slot_key": slot_key, "generated": True},
            )
            db.add(time_asset)
            await db.flush()
            logger.info("Created time announcement asset for slot %s", slot_key)
        except Exception:
            logger.warning("Failed to generate time announcement for slot %s", slot_key, exc_info=True)
            time_asset = None

    # Generate weather spot if needed
    weather_asset = existing_weather
    if not weather_asset:
        try:
            weather_data = await get_current_weather()
            weather_text = _build_weather_text(weather_data)
            weather_bytes, weather_duration = await generate_tts(weather_text)
            weather_path = f"weather/{safe_slot}_weather.mp3"
            weather_url = await upload_to_supabase(weather_bytes, weather_path)

            weather_asset = Asset(
                id=uuid.uuid4(),
                title=f"Weather Report - {slot_key}",
                artist="Kol Bramah",
                duration=weather_duration,
                file_path=weather_url,
                asset_type="spot",
                category="weather_spot",
                metadata_extra={"slot_key": slot_key, "generated": True},
            )
            db.add(weather_asset)
            await db.flush()
            logger.info("Created weather spot asset for slot %s", slot_key)
        except Exception:
            logger.warning("Failed to generate weather spot for slot %s", slot_key, exc_info=True)
            weather_asset = None

    return time_asset, weather_asset
