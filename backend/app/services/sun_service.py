"""
Sun calculation service — provides sunset/sunrise times for scheduling.
Uses the astral library for accurate astronomical calculations.
"""
import logging
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

logger = logging.getLogger(__name__)


def get_sun_times(
    latitude: float,
    longitude: float,
    timezone: str = "UTC",
    for_date: date | None = None,
) -> dict[str, datetime]:
    """
    Get sunrise, sunset, dawn, and dusk times for a given location and date.

    Returns timezone-aware datetimes in the specified timezone.
    """
    tz = ZoneInfo(timezone)
    if for_date is None:
        for_date = datetime.now(tz).date()

    location = LocationInfo(
        name="station",
        region="",
        timezone=timezone,
        latitude=latitude,
        longitude=longitude,
    )

    s = sun(location.observer, date=for_date, tzinfo=tz)
    return {
        "dawn": s["dawn"],
        "sunrise": s["sunrise"],
        "noon": s["noon"],
        "sunset": s["sunset"],
        "dusk": s["dusk"],
    }


def get_sunset_time(
    latitude: float,
    longitude: float,
    timezone: str = "UTC",
    for_date: date | None = None,
) -> time:
    """Get just the sunset time (as a time object) for a location."""
    times = get_sun_times(latitude, longitude, timezone, for_date)
    return times["sunset"].time()


def get_sunrise_time(
    latitude: float,
    longitude: float,
    timezone: str = "UTC",
    for_date: date | None = None,
) -> time:
    """Get just the sunrise time (as a time object) for a location."""
    times = get_sun_times(latitude, longitude, timezone, for_date)
    return times["sunrise"].time()


def offset_sun_time(sun_time: time, offset_minutes: int) -> time:
    """Apply a minute offset to a sun time (e.g., 30 min before sunset)."""
    dt = datetime.combine(date.today(), sun_time)
    dt += timedelta(minutes=offset_minutes)
    return dt.time()
