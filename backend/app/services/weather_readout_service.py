"""
Weather readout service — template rendering and daily readout generation.

Station automation_config.weather_readout schema:
{
    "enabled": true,
    "template": "Good morning! Currently in {city}: {temp_f} degrees and {description}...",
    "city_name": "Lakewood",
    "brand_name": "Kohl Baramah",
    "generate_time": "06:00",
    "queue_time": "07:00",
    "auto_queue": true
}
"""
import logging
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.station import Station
from app.models.weather_readout import WeatherReadout
from app.services.weather_service import get_current_weather

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = (
    "Good morning! Currently in {city}: {temp_f} degrees and {description}, "
    "winds from the {wind_direction} at {wind_speed_mph} miles per hour, "
    "humidity at {humidity} percent. "
    "Looking ahead: {forecast_text} "
    "Have a great day from {brand_name}!"
)


def render_template(
    template: str,
    weather: dict,
    city_name: str = "Lakewood",
    brand_name: str = "Kohl Baramah",
    target_date: date | None = None,
) -> str:
    """Render a weather readout template with weather data variables."""
    if target_date is None:
        target_date = date.today()

    forecast = weather.get("forecast", [])
    forecast_parts = []
    for day in forecast:
        forecast_parts.append(
            f"{day['day']}: {day['description']}, "
            f"high of {day['high']} and low of {day['low']}"
        )
    forecast_text = ". ".join(forecast_parts) + "." if forecast_parts else ""

    variables = {
        "temp_f": str(weather.get("temp_f", "")),
        "description": weather.get("description", ""),
        "wind_speed_mph": str(weather.get("wind_speed_mph", "")),
        "wind_direction": weather.get("wind_direction", ""),
        "humidity": str(weather.get("humidity", "")),
        "city": city_name,
        "brand_name": brand_name,
        "forecast_text": forecast_text,
        "date": target_date.strftime("%B %d, %Y"),
        "day_of_week": target_date.strftime("%A"),
    }

    # Add per-day forecast variables
    for i, day in enumerate(forecast[:3], start=1):
        variables[f"day{i}_name"] = day.get("day", "")
        variables[f"day{i}_high"] = str(day.get("high", ""))
        variables[f"day{i}_low"] = str(day.get("low", ""))
        variables[f"day{i}_desc"] = day.get("description", "")

    # Use safe formatting — ignore missing keys
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", value)

    return result


async def generate_readout_for_station(
    db: AsyncSession,
    station: Station,
    target_date: date | None = None,
) -> WeatherReadout | None:
    """Generate a weather readout for a station on a given date.

    Skips if a readout already exists for that station+date.
    Returns the created WeatherReadout or None if skipped/failed.
    """
    if target_date is None:
        target_date = date.today()

    config = (station.automation_config or {}).get("weather_readout", {})
    if not config.get("enabled"):
        return None

    # Check if readout already exists for this station+date
    existing = await db.execute(
        select(WeatherReadout).where(
            WeatherReadout.station_id == station.id,
            WeatherReadout.readout_date == target_date,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        return None

    # Fetch weather data
    lat = station.latitude or 40.0968
    lon = station.longitude or -74.2179
    tz_name = station.timezone or "America/New_York"

    try:
        weather = await get_current_weather(lat=lat, lon=lon, timezone_name=tz_name)
    except Exception:
        logger.warning("Failed to fetch weather for station %s", station.id, exc_info=True)
        return None

    # Render template
    template = config.get("template", DEFAULT_TEMPLATE)
    city_name = config.get("city_name", "Lakewood")
    brand_name = config.get("brand_name", "Kohl Baramah")
    script_text = render_template(template, weather, city_name, brand_name, target_date)

    # Parse queue_time
    queue_time_val = None
    qt_str = config.get("queue_time")
    if qt_str:
        try:
            parts = qt_str.split(":")
            queue_time_val = time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            pass

    readout = WeatherReadout(
        station_id=station.id,
        readout_date=target_date,
        script_text=script_text,
        weather_data=weather,
        status="pending",
        queue_time=queue_time_val,
        generated_by="auto",
    )
    db.add(readout)
    await db.flush()

    logger.info("Generated weather readout for station %s on %s", station.name, target_date)
    return readout
