"""The weather API and ElevenLabs must not be called in a loop (the OpenWeatherMap key was blocked in Nov 2026)."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import weather_service

SAMPLE = {"temp_f": 50, "description": "clear sky", "wind_speed_mph": 5, "wind_direction": "north",
          "humidity": 40, "forecast": []}


@pytest.mark.asyncio
async def test_weather_is_cached_per_location():
    fetch = AsyncMock(return_value=SAMPLE)
    with patch.object(weather_service, "_fetch_weather", fetch):
        for _ in range(50):
            assert await weather_service.get_current_weather() == SAMPLE
        await weather_service.get_current_weather(lat=31.77, lon=35.21, timezone_name="Asia/Jerusalem")
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_failure_is_not_retried_until_the_backoff_passes():
    resp = httpx.Response(429, request=httpx.Request("GET", "https://api.openweathermap.org/data/2.5/weather"))
    fetch = AsyncMock(side_effect=httpx.HTTPStatusError("429", request=resp.request, response=resp))
    with patch.object(weather_service, "_fetch_weather", fetch):
        for _ in range(20):
            with pytest.raises(httpx.HTTPStatusError):
                await weather_service.get_current_weather()
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_failed_generation_pauses_instead_of_retrying_every_slot(db_session: AsyncSession):
    """ElevenLabs refusing must not make the scheduler call it (and the weather API) for every slot on every pass."""
    from app.services.weather_spot_service import get_or_create_weather_spot_assets

    tts = AsyncMock(side_effect=RuntimeError("400 Bad Request"))
    weather = AsyncMock(return_value=SAMPLE)
    with patch("app.services.weather_spot_service.settings.ELEVENLABS_API_KEY", "k"), \
         patch("app.services.weather_spot_service.settings.ELEVENLABS_VOICE_ID", "v"), \
         patch("app.services.weather_spot_service.generate_tts", tts), \
         patch("app.services.weather_spot_service.get_current_weather", weather):
        for h in range(48):                       # one scheduler pass over 48 hours
            t, w = await get_or_create_weather_spot_assets(db_session, f"2026-11-17T{h % 24:02d}")
            assert t is None and w is None
    assert tts.await_count == 1
    assert weather.await_count == 0
