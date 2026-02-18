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


async def get_current_weather() -> dict:
    """Fetch current weather for Lakewood, NJ from OpenWeatherMap.

    Returns dict with keys: temp_f, description, wind_speed_mph,
    wind_direction, humidity.
    """
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": "40.0968",
        "lon": "-74.2179",
        "units": "imperial",
        "appid": settings.OPENWEATHERMAP_API_KEY,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    weather_desc = data["weather"][0]["description"] if data.get("weather") else "unknown conditions"
    wind_deg = data.get("wind", {}).get("deg", 0)

    return {
        "temp_f": round(data["main"]["temp"]),
        "description": weather_desc,
        "wind_speed_mph": round(data["wind"]["speed"]),
        "wind_direction": _deg_to_cardinal(wind_deg),
        "humidity": data["main"]["humidity"],
    }
