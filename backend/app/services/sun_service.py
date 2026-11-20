"""
Sun calculation service — provides sunset/sunrise times for scheduling.
Uses the astral library for accurate astronomical calculations.
"""
import logging
import math
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import SunDirection, sun, time_at_elevation

logger = logging.getLogger(__name__)

# Jerusalem's calendars give sunset as seen from the city's height, about 5 minutes after
# sunset at sea level: candle lighting (40 min before it) was 4:02 PM on 2026-11-20 by
# Hebcal and the printed luach, while the station went silent at 3:57 (Shmuel asked).
# A station in an Israel time zone keeps Jerusalem's height, as Hebcal does (786 m, its
# Jerusalem); elsewhere sunset stays at sea level, as US calendars give it (Lakewood
# candle lighting 4:18 PM on 2026-11-20, same as Hebcal).
ISRAEL_ZONES = {"Asia/Jerusalem", "Asia/Tel_Aviv", "Israel"}
ISRAEL_ELEVATION_M = 786.0
EARTH_RADIUS_KM = 6356.9   # as NOAA / KosherJava / Hebcal use for the horizon's dip
SUN_ZENITH_DEPRESSION = 0.833  # refraction and the sun's half width at the horizon


def elevation_for(timezone: str | None) -> float:
    return ISRAEL_ELEVATION_M if (timezone or "") in ISRAEL_ZONES else 0.0


def _horizon(observer, for_date: date, tz, elevation_m: float, direction) -> datetime:
    """Sunrise or sunset seen from `elevation_m` above the plain: the sun is that much
    further below the horizon (the dip), computed as Hebcal does (within a second over a
    year of Jerusalem Fridays)."""
    dip = math.degrees(math.acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + elevation_m / 1000.0)))
    return time_at_elevation(observer, -(SUN_ZENITH_DEPRESSION + dip), for_date, direction,
                             tzinfo=tz, with_refraction=False)


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
    sunrise, sunset = s["sunrise"], s["sunset"]
    height = elevation_for(timezone)
    if height > 0:
        sunrise = _horizon(location.observer, for_date, tz, height, SunDirection.RISING)
        sunset = _horizon(location.observer, for_date, tz, height, SunDirection.SETTING)
    return {
        "dawn": s["dawn"],
        "sunrise": sunrise,
        "noon": s["noon"],
        "sunset": sunset,
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
