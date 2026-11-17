import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# OpenWeatherMap's free plan allows 60 calls a minute, and each fetch costs two
# calls. Callers (the scheduler looks 48 hours ahead every few seconds) may ask
# far more often than that, so every answer is cached per location, and a
# failure is remembered too so a broken key or a 429 is not retried in a loop.
CACHE_SECONDS = 30 * 60
FAILURE_BACKOFF_SECONDS = 15 * 60
RATE_LIMIT_BACKOFF_SECONDS = 60 * 60
_cache: dict[tuple, tuple[float, dict]] = {}
_failed: dict[tuple, tuple[float, Exception]] = {}


class WeatherNotConfiguredError(RuntimeError):
    """OPENWEATHERMAP_API_KEY is not set: there is no weather to fetch."""


def clear_weather_cache() -> None:
    _cache.clear()
    _failed.clear()

WIND_DIRECTIONS = [
    "north", "north-northeast", "northeast", "east-northeast",
    "east", "east-southeast", "southeast", "south-southeast",
    "south", "south-southwest", "southwest", "west-southwest",
    "west", "west-northwest", "northwest", "north-northwest",
]


def _deg_to_cardinal(deg: float) -> str:
    """Convert wind degree (0-360) to cardinal direction."""
    idx = round(deg / 22.5) % 16
    return WIND_DIRECTIONS[idx]


async def get_current_weather(
    lat: float = 40.0968,
    lon: float = -74.2179,
    timezone_name: str = "America/New_York",
) -> dict:
    """Fetch current weather + 3-day forecast from OpenWeatherMap.

    Returns dict with keys: temp_f, description, wind_speed_mph,
    wind_direction, humidity, forecast (list of day dicts).

    Cached for CACHE_SECONDS per location. After a failure the same error is
    raised again without calling the API until the back-off has passed.
    """
    if not settings.weather_enabled:
        # No key: calling the API anyway only earned a 401 and an hour's back-off
        raise WeatherNotConfiguredError("Weather is not set up (OPENWEATHERMAP_API_KEY is empty)")
    key = (round(lat, 3), round(lon, 3), timezone_name)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    failed = _failed.get(key)
    if failed and now < failed[0]:
        # Drop the stored traceback first: re-raising the same object would keep
        # appending frames to it on every call during the back-off.
        raise failed[1].with_traceback(None)
    try:
        result = await _fetch_weather(lat, lon, timezone_name)
    except Exception as e:
        limited = isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 429)
        wait = RATE_LIMIT_BACKOFF_SECONDS if limited else FAILURE_BACKOFF_SECONDS
        _failed[key] = (now + wait, e)
        logger.warning("Weather fetch failed (%s); not retrying for %d minutes", e, wait // 60)
        raise
    _failed.pop(key, None)
    _cache[key] = (now, result)
    return result


async def _fetch_weather(lat: float, lon: float, timezone_name: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Current weather
        resp = await client.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": str(lat), "lon": str(lon), "units": "imperial", "appid": settings.OPENWEATHERMAP_API_KEY},
        )
        resp.raise_for_status()
        data = resp.json()

        # 5-day/3-hour forecast (we'll extract daily summaries)
        forecast_resp = await client.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"lat": str(lat), "lon": str(lon), "units": "imperial", "appid": settings.OPENWEATHERMAP_API_KEY},
        )
        forecast_resp.raise_for_status()
        forecast_data = forecast_resp.json()

    weather_desc = data["weather"][0]["description"] if data.get("weather") else "unknown conditions"
    wind_deg = data.get("wind", {}).get("deg", 0)

    forecast = daily_forecast(forecast_data.get("list", []), timezone_name)

    return {
        "temp_f": round(data["main"]["temp"]),
        "description": weather_desc,
        "wind_speed_mph": round(data["wind"]["speed"]),
        "wind_direction": _deg_to_cardinal(wind_deg),
        "humidity": data["main"]["humidity"],
        "forecast": forecast,
    }


def daily_forecast(items: list[dict], timezone_name: str, now=None, days: int = 3) -> list[dict]:
    """The next `days` days after today from OpenWeatherMap's 3-hour forecast list.

    Days are the station's own calendar days (today left out); high is the highest
    temp_max and low the lowest temp_min of that day's 3-hour values; the description
    is the day's first one."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(timezone_name)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    by_day: dict = {}
    for item in sorted(items, key=lambda i: i["dt"]):
        d = datetime.fromtimestamp(item["dt"], tz=timezone.utc).astimezone(zone).date()
        if d <= today:
            continue
        info = by_day.setdefault(d, {"highs": [], "lows": [], "descriptions": []})
        info["highs"].append(item["main"]["temp_max"])
        info["lows"].append(item["main"]["temp_min"])
        desc = item["weather"][0]["description"] if item.get("weather") else ""
        if desc and desc not in info["descriptions"]:
            info["descriptions"].append(desc)
    return [
        {
            "day": d.strftime("%A"),
            "high": round(max(info["highs"])),
            "low": round(min(info["lows"])),
            "description": info["descriptions"][0] if info["descriptions"] else "unknown",
        }
        for d, info in sorted(by_day.items())[:days]
    ]
