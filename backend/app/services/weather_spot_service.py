import logging
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.asset import Asset
from app.services.supabase_storage_service import upload_to_supabase
from app.services.tts_service import generate_tts
from app.services.weather_service import get_current_weather

logger = logging.getLogger(__name__)

# ── Customizable sign-in / sign-out texts ──
# Only these should be edited for branding. The weather body is automated.
# How the station's name is written wherever people read it (Library artist, titles).
# The spoken texts below keep the voice spelling "Kohl Baramah".
STATION_DISPLAY_NAME = "Kol B'ramah"

WEATHER_SIGN_IN = "Here's your Kohl Baramah weather update."
WEATHER_SIGN_OUT = (
    "And have a great day, and stay safe, "
    "from YOUR Kohl Baramah family... weather room."
)

# ── Word replacements ──
# Applied to ALL generated TTS text (time + weather).
# Longer phrases MUST come before shorter ones (e.g. "Saturday night" before "Saturday").
WORD_REPLACEMENTS: list[tuple[str, str]] = [
    ("Saturday night", "Motzie Shabbos"),
    ("Saturday",       "Shabbos"),
    ("Sunday",         "Sunday"),          # no change — placeholder for easy editing
]


def _apply_word_replacements(text: str) -> str:
    """Apply word replacements to TTS text (case-insensitive, preserves flow)."""
    for original, replacement in WORD_REPLACEMENTS:
        # Case-insensitive replace, keeping surrounding context
        pattern = re.compile(re.escape(original), re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text


def _utc_to_eastern(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to US/Eastern (handles EST/EDT)."""
    return utc_dt.astimezone(ZoneInfo(HOME_TIMEZONE))


# The station's home. A station whose time zone was never set (left at the
# default "UTC") announces this time, as every station did before time zones.
HOME_TIMEZONE = "America/New_York"


def announcement_zone(timezone_name: str | None) -> ZoneInfo:
    """The clock a station's time announcements read: its own time zone."""
    if not timezone_name or timezone_name.upper() in ("UTC", "ETC/UTC"):
        return ZoneInfo(HOME_TIMEZONE)
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo(HOME_TIMEZONE)


async def station_announcement_zone(db: AsyncSession, station_id) -> ZoneInfo:
    from app.models.station import Station

    name = (await db.execute(select(Station.timezone).where(Station.id == station_id))).scalar_one_or_none()
    return announcement_zone(name)


def slot_key_for(at_utc: datetime, zone: ZoneInfo) -> str:
    """The hourly announcement key ("2026-11-16T08") for a moment, in the station's own time."""
    return at_utc.astimezone(zone).strftime("%Y-%m-%dT%H")


def clip_minute(asset: Asset) -> int:
    """30 for a half-hour clip ("The time is 10:30 ..."), 0 for the top of the hour."""
    meta = asset.metadata_extra if isinstance(asset.metadata_extra, dict) else {}
    return 30 if str(meta.get("time_minute") or "") == "30" else 0


def half_hour_time_check(automation_config: dict | None) -> bool:
    """Station setting automation_config.half_hour_time_check: at hh:30 it plays the
    uploaded half-hour time clip before the :30 weather. Off by default; with no
    half-hour clip recorded for the station's region nothing is played."""
    return (automation_config or {}).get("half_hour_time_check") is True


def clip_hour(asset: Asset) -> int | None:
    """The hour of the day a time announcement says (uploaded or generated clip)."""
    meta = asset.metadata_extra or {}
    # Midnight is 0: test for None, not falsy, or the midnight clip has no hour
    raw = meta.get("time_of_day")
    if raw is None or raw == "":
        raw = (meta.get("slot_key") or "")[11:13]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# Station sign-off for the hourly time announcement. Spelled for the voice
# engine ("Kohl Baramah" is how it pronounces Kol Berama correctly).
TIME_ANNOUNCEMENT_TEMPLATE = "The time is {time} on Radio {brand}, W M D I 107.9, the highest frequency."


def _build_time_text(eastern_now: datetime, brand_name: str = "Kohl Baramah") -> str:
    hour = eastern_now.strftime("%I").lstrip("0")
    minute = eastern_now.strftime("%M")
    ampm = eastern_now.strftime("%p")
    # On the hour say "3 PM", not "3:00 PM".
    spoken = f"{hour} {ampm}" if minute == "00" else f"{hour}:{minute} {ampm}"
    text = TIME_ANNOUNCEMENT_TEMPLATE.format(time=spoken, brand=brand_name)
    return _apply_word_replacements(text)


def _build_weather_text(weather: dict, city_name: str = "Lakewood") -> str:
    """Build weather readout: sign-in + automated body + sign-out.

    The body is fully automated from weather data.
    Only WEATHER_SIGN_IN and WEATHER_SIGN_OUT should be customized.
    Word replacements are applied to the entire text.
    """
    parts = [WEATHER_SIGN_IN]

    # Automated body — current conditions
    parts.append(
        f"Currently in {city_name}: {weather['temp_f']} degrees and {weather['description']}, "
        f"winds from the {weather['wind_direction']} at {weather['wind_speed_mph']} miles per hour."
    )

    # Automated body — forecast
    forecast = weather.get("forecast", [])
    if forecast:
        parts.append("Looking ahead:")
        for day in forecast:
            parts.append(
                f"{day['day']}: {day['description']}, "
                f"with a high of {day['high']} and a low of {day['low']}."
            )

    parts.append(WEATHER_SIGN_OUT)

    text = " ".join(parts)
    return _apply_word_replacements(text)


def _slot_time(slot_key: str, fallback: datetime) -> datetime:
    """The Eastern clock time a slot key ("2024-01-15T15") stands for.

    Announcements are generated up to 48 hours before they air, so the spoken
    time must come from the slot, never from the moment of generation.
    """
    for fmt in ("%Y-%m-%dT%H", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H-%M"):
        try:
            return datetime.strptime(slot_key, fmt).replace(tzinfo=fallback.tzinfo)
        except ValueError:
            continue
    return fallback


# Weather is per region, so a station abroad plays its own city's report. A station
# names its region in automation_config["weather_region"]; no setting means the
# default region (Lakewood), which is also what every spot made before regions
# existed belongs to (they carry no "region" in their metadata).
DEFAULT_WEATHER_REGION = "lakewood"


def normalize_region(region: str | None) -> str:
    region = (region or "").strip().lower()
    return region or DEFAULT_WEATHER_REGION


def station_weather_region(automation_config: dict | None) -> str:
    """The weather region a station's config asks for (default region if unset)."""
    return normalize_region((automation_config or {}).get("weather_region"))


async def weather_region_for_station(db: AsyncSession, station_id) -> str:
    from app.models.station import Station

    station = await db.get(Station, station_id)
    return station_weather_region(station.automation_config if station else None)


def _region_filter(region: str):
    stored = Asset.metadata_extra["region"].astext
    if region == DEFAULT_WEATHER_REGION:
        return or_(stored.is_(None), stored == DEFAULT_WEATHER_REGION)
    return stored == region


async def find_weather_spot_asset(
    db: AsyncSession, weather_key: str, region: str | None = None
) -> Asset | None:
    """The weather spot asset for one day (weather_key like "2024-01-15") and region, if any."""
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "spot",
            Asset.category == "weather_spot",
            Asset.metadata_extra["slot_key"].astext == weather_key,
            _region_filter(normalize_region(region)),
        ).limit(1)
    )
    return result.scalar_one_or_none()


def _minute_filter(minute: int):
    stored = Asset.metadata_extra["time_minute"].astext
    if minute == 30:
        return stored == "30"
    # Clips from before half-hour clips existed have no minute: they are hh:00
    return or_(stored.is_(None), stored != "30")


async def find_own_time_clip(
    db: AsyncSession, hour: int, region: str | None = None, minute: int = 0
) -> Asset | None:
    """The uploaded "the time is" clip recorded for this region and hour (0-23), if any.

    minute 30: the half-hour clip ("The time is 10:30 ..."), else the top of the hour.

    Regions are the same as the weather's (automation_config.weather_region), so
    KBR Jerusalem can have its own set; clips made before regions existed belong
    to the default region.
    """
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "jingle",
            Asset.category == "time_announcement",
            Asset.metadata_extra["time_of_day"].astext == f"{hour:02d}",
            _minute_filter(minute),
            _region_filter(normalize_region(region)),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def find_time_clip_asset(
    db: AsyncSession, hour: int, region: str | None = None, minute: int = 0
) -> Asset | None:
    """The clip a station of this region plays at this hour: its own, else the default set's.

    Unlike weather, the default set is a fair fallback: the hour is the station's
    own local hour either way, only the recording is shared.
    """
    region = normalize_region(region)
    own = await find_own_time_clip(db, hour, region, minute)
    if own is None and region != DEFAULT_WEATHER_REGION:
        own = await find_own_time_clip(db, hour, DEFAULT_WEATHER_REGION, minute)
    return own


def _decoded_duration(audio_bytes: bytes) -> float:
    """Audio length found by decoding the whole file.

    The normalized MP3 comes out of an FFmpeg pipe, so it has no length header
    and FFmpeg reports "Duration: N/A" for it. The progress line of a full
    decode ("time=00:00:44.78") still gives the real length.
    """
    import subprocess

    try:
        result = subprocess.run(
            [settings.FFMPEG_PATH, "-i", "pipe:0", "-f", "null", "-"],
            input=audio_bytes, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr.decode("utf-8", errors="replace"))
    if not times:
        return 0.0
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


async def _store_upload(audio_bytes: bytes, path_stem: str) -> tuple[str, float]:
    """Normalize an uploaded recording, store it, return (public_url, duration)."""
    from app.services.audio_analysis_service import normalize_audio_loudness
    from app.services.silence_service import get_audio_duration

    audio_bytes = normalize_audio_loudness(audio_bytes)
    duration = get_audio_duration(audio_bytes) or _decoded_duration(audio_bytes)
    if duration <= 0:
        raise ValueError("Uploaded file is not readable audio")
    # A new path on every upload: the public URL is cached, so re-using the
    # path would keep serving the old recording after a replacement.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    url = await upload_to_supabase(audio_bytes, f"{path_stem}_{stamp}.mp3")
    return url, duration


def _upload_meta(source: str, script: str | None, **keys: str) -> dict:
    extra = {
        **keys,
        "generated": False,
        "source": source,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if script:
        extra["script"] = script
    return extra


async def store_uploaded_weather_spot(
    db: AsyncSession,
    weather_key: str,
    audio_bytes: bytes,
    *,
    source: str = "shprach",
    script: str | None = None,
    region: str | None = None,
) -> tuple[Asset, bool]:
    """Make an uploaded, ready-made recording the weather spot for one day and region.

    The day's weather spot is a single asset keyed by date, and the hourly
    scheduler pre-queues that asset up to 48 hours ahead. So when one already
    exists (usually an ElevenLabs one made by the scheduler) it is updated in
    place: queue entries that already point at it then play the uploaded audio.
    With no asset yet, one is created and get_or_create_weather_spot_assets()
    finds it later instead of calling ElevenLabs.

    Returns (asset, replaced) where replaced says an existing spot was overwritten.
    """
    region = normalize_region(region)
    own = region != DEFAULT_WEATHER_REGION
    url, duration = await _store_upload(
        audio_bytes, f"weather/{weather_key}{'_' + region if own else ''}_weather_{source}"
    )
    asset = await find_weather_spot_asset(db, weather_key, region)
    replaced = asset is not None
    if asset is None:
        asset = Asset(
            id=uuid.uuid4(),
            title=f"Weather Report - {weather_key}" + (f" ({region.title()})" if own else ""),
            artist=STATION_DISPLAY_NAME,
            file_path=url,
            asset_type="spot",
            category="weather_spot",
        )
        db.add(asset)
    asset.file_path = url
    asset.duration = duration
    # Replaced wholesale on purpose: any cached analysis/trim points in the old
    # metadata belong to the old recording.
    asset.metadata_extra = _upload_meta(source, script, slot_key=weather_key, region=region)
    await db.flush()
    logger.info("Stored uploaded %s weather spot for %s, day %s (replaced=%s)", source, region, weather_key, replaced)
    return asset, replaced


async def store_uploaded_time_clip(
    db: AsyncSession,
    hour: int,
    audio_bytes: bytes,
    *,
    source: str = "shprach",
    script: str | None = None,
    region: str | None = None,
    minute: int = 0,
) -> tuple[Asset, bool]:
    """Store the reusable "the time is" recording for an hour of the day (0-23) and region.

    minute 30 stores the half-hour clip ("The time is 10:30 ..."), played at hh:30
    on stations with half_hour_time_check on.

    Unlike the per-slot ElevenLabs clips, one uploaded clip per hour is reused
    every day. An existing clip for the hour is updated in place, so entries
    already queued with it play the new recording.
    """
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 0-23")
    if minute not in (0, 30):
        raise ValueError("minute must be 0 or 30")
    region = normalize_region(region)
    own = region != DEFAULT_WEATHER_REGION
    url, duration = await _store_upload(
        audio_bytes, f"time/{region + '_' if own else ''}{hour:02d}{'_30' if minute else ''}_{source}"
    )
    asset = await find_own_time_clip(db, hour, region, minute)
    replaced = asset is not None
    if asset is None:
        asset = Asset(
            id=uuid.uuid4(),
            title=f"Time Announcement - {hour:02d}:{minute:02d}" + (f" ({region.title()})" if own else ""),
            artist=STATION_DISPLAY_NAME,
            file_path=url,
            asset_type="jingle",
            category="time_announcement",
        )
        db.add(asset)
    asset.file_path = url
    asset.duration = duration
    keys = {"time_of_day": f"{hour:02d}", "region": region}
    if minute:
        keys["time_minute"] = f"{minute:02d}"
    asset.metadata_extra = _upload_meta(source, script, **keys)
    await db.flush()
    logger.info("Stored uploaded %s time clip for %s, %02d:%02d (replaced=%s)", source, region, hour, minute, replaced)
    return asset, replaced


# The scheduler asks for up to 48 hourly slots every few seconds. When
# generation fails (ElevenLabs refusing, weather API blocked), retrying each
# slot on every pass calls both services in a tight loop; that is how the
# OpenWeatherMap key got blocked in November 2026. After a failure, generation
# pauses for a while for every slot; uploaded clips are still returned.
GENERATION_PAUSE_SECONDS = 15 * 60
_generation_paused_until = 0.0


def _generation_paused() -> bool:
    import time

    return time.monotonic() < _generation_paused_until


def _pause_generation() -> None:
    import time

    global _generation_paused_until
    _generation_paused_until = time.monotonic() + GENERATION_PAUSE_SECONDS
    logger.warning("Announcement generation paused for %d minutes after a failure", GENERATION_PAUSE_SECONDS // 60)


def resume_generation() -> None:
    global _generation_paused_until
    _generation_paused_until = 0.0


async def get_or_create_weather_spot_assets(
    db: AsyncSession, slot_key: str, region: str | None = None
) -> tuple[Asset | None, Asset | None]:
    """Get or create time-announcement and weather-spot assets.

    Time announcements are keyed per-hour (slot_key like "2024-01-15T15").
    Weather spots are cached per-day (date portion only, e.g. "2024-01-15").

    Args:
        db: Database session.
        slot_key: Dedup key like "2024-01-15T15" (hourly).

    Returns:
        (time_asset, weather_asset) — either may be None on failure.

    Uploaded recordings win: the reusable time clip for the slot's hour and the
    day's uploaded weather spot are used when they exist. ElevenLabs is only the
    fallback for what is missing, and only when a key is configured.

    region: the station's weather region (station_weather_region). Only the
    default region's weather is ever generated; another region has weather only
    when its own spot was uploaded, so a station never plays another city's report.
    """
    region = normalize_region(region)
    # Weather uses daily key (just the date part)
    weather_key = slot_key.split("T")[0] if "T" in slot_key else slot_key

    # Check if assets already exist
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "jingle",
            Asset.category == "time_announcement",
            Asset.metadata_extra["slot_key"].astext == slot_key,
        ).limit(1)
    )
    existing_time = result.scalar_one_or_none()

    # An uploaded clip for the slot's hour (reused every day) wins over a
    # per-slot generated one.
    eastern_now = _utc_to_eastern(datetime.now(timezone.utc))
    uploaded_time = await find_time_clip_asset(db, _slot_time(slot_key, eastern_now).hour, region)
    if uploaded_time is not None:
        existing_time = uploaded_time

    existing_weather = await find_weather_spot_asset(db, weather_key, region)
    # No weather key: no weather to generate. Trying anyway failed every time, and each
    # failure paused the time announcements' generation too.
    generate_weather = region == DEFAULT_WEATHER_REGION and settings.weather_enabled

    if existing_time and (existing_weather or not generate_weather):
        return existing_time, existing_weather
    if not settings.elevenlabs_enabled or _generation_paused():
        return existing_time, existing_weather

    safe_slot = slot_key.replace(":", "-")
    safe_weather_key = weather_key.replace(":", "-")

    # Generate time announcement if needed (per-hour)
    time_asset = existing_time
    if not time_asset:
        try:
            time_text = _build_time_text(_slot_time(slot_key, eastern_now))
            time_bytes, time_duration = await generate_tts(time_text)
            from app.services.audio_analysis_service import normalize_audio_loudness
            time_bytes = normalize_audio_loudness(time_bytes)
            time_path = f"weather/{safe_slot}_time.mp3"
            time_url = await upload_to_supabase(time_bytes, time_path)

            time_asset = Asset(
                id=uuid.uuid4(),
                title=f"Time Announcement - {slot_key}",
                artist=STATION_DISPLAY_NAME,
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
            _pause_generation()
            return None, existing_weather

    # Generate weather spot if needed (per-day — reused until midnight)
    weather_asset = existing_weather
    if not weather_asset and generate_weather:
        try:
            weather_data = await get_current_weather()
            weather_text = _build_weather_text(weather_data)
            weather_bytes, weather_duration = await generate_tts(weather_text)
            from app.services.audio_analysis_service import normalize_audio_loudness
            weather_bytes = normalize_audio_loudness(weather_bytes)
            weather_path = f"weather/{safe_weather_key}_weather.mp3"
            weather_url = await upload_to_supabase(weather_bytes, weather_path)

            weather_asset = Asset(
                id=uuid.uuid4(),
                title=f"Weather Report - {weather_key}",
                artist=STATION_DISPLAY_NAME,
                duration=weather_duration,
                file_path=weather_url,
                asset_type="spot",
                category="weather_spot",
                metadata_extra={"slot_key": weather_key, "generated": True},
            )
            db.add(weather_asset)
            await db.flush()
            logger.info("Created weather spot asset for day %s", weather_key)
        except Exception:
            logger.warning("Failed to generate weather spot for day %s", weather_key, exc_info=True)
            _pause_generation()
            weather_asset = None

    return time_asset, weather_asset
