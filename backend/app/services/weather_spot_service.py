import logging
import re
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

# ── Customizable sign-in / sign-out texts ──
# Only these should be edited for branding. The weather body is automated.
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
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    return utc_dt.astimezone(ZoneInfo("America/New_York"))


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


async def find_weather_spot_asset(db: AsyncSession, weather_key: str) -> Asset | None:
    """The weather spot asset for one day (weather_key like "2024-01-15"), if any."""
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "spot",
            Asset.category == "weather_spot",
            Asset.metadata_extra["slot_key"].astext == weather_key,
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def find_time_clip_asset(db: AsyncSession, hour: int) -> Asset | None:
    """The reusable uploaded "the time is" clip for an hour of the day (0-23), if any."""
    result = await db.execute(
        select(Asset).where(
            Asset.asset_type == "jingle",
            Asset.category == "time_announcement",
            Asset.metadata_extra["time_of_day"].astext == f"{hour:02d}",
        ).limit(1)
    )
    return result.scalar_one_or_none()


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
) -> tuple[Asset, bool]:
    """Make an uploaded, ready-made recording the weather spot for one day.

    The day's weather spot is a single asset keyed by date, and the hourly
    scheduler pre-queues that asset up to 48 hours ahead. So when one already
    exists (usually an ElevenLabs one made by the scheduler) it is updated in
    place: queue entries that already point at it then play the uploaded audio.
    With no asset yet, one is created and get_or_create_weather_spot_assets()
    finds it later instead of calling ElevenLabs.

    Returns (asset, replaced) where replaced says an existing spot was overwritten.
    """
    url, duration = await _store_upload(audio_bytes, f"weather/{weather_key}_weather_{source}")
    asset = await find_weather_spot_asset(db, weather_key)
    replaced = asset is not None
    if asset is None:
        asset = Asset(
            id=uuid.uuid4(),
            title=f"Weather Report - {weather_key}",
            artist="Kohl Baramah",
            file_path=url,
            asset_type="spot",
            category="weather_spot",
        )
        db.add(asset)
    asset.file_path = url
    asset.duration = duration
    # Replaced wholesale on purpose: any cached analysis/trim points in the old
    # metadata belong to the old recording.
    asset.metadata_extra = _upload_meta(source, script, slot_key=weather_key)
    await db.flush()
    logger.info("Stored uploaded %s weather spot for day %s (replaced=%s)", source, weather_key, replaced)
    return asset, replaced


async def store_uploaded_time_clip(
    db: AsyncSession,
    hour: int,
    audio_bytes: bytes,
    *,
    source: str = "shprach",
    script: str | None = None,
) -> tuple[Asset, bool]:
    """Store the reusable "the time is" recording for an hour of the day (0-23).

    Unlike the per-slot ElevenLabs clips, one uploaded clip per hour is reused
    every day. An existing clip for the hour is updated in place, so entries
    already queued with it play the new recording.
    """
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 0-23")
    url, duration = await _store_upload(audio_bytes, f"time/{hour:02d}_{source}")
    asset = await find_time_clip_asset(db, hour)
    replaced = asset is not None
    if asset is None:
        asset = Asset(
            id=uuid.uuid4(),
            title=f"Time Announcement - {hour:02d}:00",
            artist="Kohl Baramah",
            file_path=url,
            asset_type="jingle",
            category="time_announcement",
        )
        db.add(asset)
    asset.file_path = url
    asset.duration = duration
    asset.metadata_extra = _upload_meta(source, script, time_of_day=f"{hour:02d}")
    await db.flush()
    logger.info("Stored uploaded %s time clip for hour %02d (replaced=%s)", source, hour, replaced)
    return asset, replaced


async def get_or_create_weather_spot_assets(
    db: AsyncSession, slot_key: str
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
    """
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
    uploaded_time = await find_time_clip_asset(db, _slot_time(slot_key, eastern_now).hour)
    if uploaded_time is not None:
        existing_time = uploaded_time

    existing_weather = await find_weather_spot_asset(db, weather_key)

    if existing_time and existing_weather:
        return existing_time, existing_weather
    if not settings.elevenlabs_enabled:
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
                artist="Kohl Baramah",
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

    # Generate weather spot if needed (per-day — reused until midnight)
    weather_asset = existing_weather
    if not weather_asset:
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
                artist="Kohl Baramah",
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
            weather_asset = None

    return time_asset, weather_asset
