import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

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
    """

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

    # Build daily forecast from 3-hour data
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    eastern = ZoneInfo(timezone_name)
    today = datetime.now(timezone.utc).astimezone(eastern).date()
    days: dict[str, dict] = {}

    for item in forecast_data.get("list", []):
        dt = datetime.fromtimestamp(item["dt"], tz=timezone.utc).astimezone(eastern)
        day_key = dt.strftime("%A")
        d = dt.date()
        if d <= today:
            continue
        if day_key not in days:
            days[day_key] = {"highs": [], "lows": [], "descriptions": []}
        days[day_key]["highs"].append(item["main"]["temp_max"])
        days[day_key]["lows"].append(item["main"]["temp_min"])
        desc = item["weather"][0]["description"] if item.get("weather") else ""
        if desc and desc not in days[day_key]["descriptions"]:
            days[day_key]["descriptions"].append(desc)

    forecast = []
    for day_name, info in list(days.items())[:3]:
        forecast.append({
            "day": day_name,
            "high": round(max(info["highs"])),
            "low": round(min(info["lows"])),
            "description": info["descriptions"][0] if info["descriptions"] else "unknown",
        })

    return {
        "temp_f": round(data["main"]["temp"]),
        "description": weather_desc,
        "wind_speed_mph": round(data["wind"]["speed"]),
        "wind_direction": _deg_to_cardinal(wind_deg),
        "humidity": data["main"]["humidity"],
        "forecast": forecast,
    }
