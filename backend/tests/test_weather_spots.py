import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset

DAY = "2026-11-16"


def _patches(duration: float = 44.8):
    """No FFmpeg or Supabase in tests: pass audio through and fake the upload URL."""
    return (
        patch("app.services.audio_analysis_service.normalize_audio_loudness", side_effect=lambda b: b),
        patch("app.services.silence_service.get_audio_duration", return_value=duration),
        patch(
            "app.services.weather_spot_service.upload_to_supabase",
            new_callable=AsyncMock,
            side_effect=lambda data, path: f"https://storage.test/{path}",
        ),
    )


async def _put(client: AsyncClient, headers: dict, day: str = DAY, **data):
    return await client.put(
        f"/api/v1/weather-spots/{day}",
        files={"file": ("wk.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        data=data,
        headers=headers,
    )


@pytest.mark.asyncio
async def test_upload_creates_the_days_weather_spot(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        resp = await _put(client, auth_headers, script="Today, sunny.")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replaced"] is False
    assert body["source"] == "shprach"
    assert body["duration"] == 44.8
    assert body["script"] == "Today, sunny."
    assert f"weather/{DAY}_weather_shprach_" in body["file_url"]

    asset = (await db_session.execute(select(Asset))).scalar_one()
    assert asset.asset_type == "spot" and asset.category == "weather_spot"
    assert asset.metadata_extra["slot_key"] == DAY

    got = await client.get(f"/api/v1/weather-spots/{DAY}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["asset_id"] == body["asset_id"]


@pytest.mark.asyncio
async def test_upload_replaces_existing_spot_in_place(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    """An ElevenLabs spot already queued for the day keeps its id, so queued entries play the new audio."""
    old = Asset(
        id=uuid.uuid4(), title=f"Weather Report - {DAY}", artist="Kohl Baramah", duration=20.0,
        file_path="https://storage.test/weather/old.mp3", asset_type="spot", category="weather_spot",
        metadata_extra={"slot_key": DAY, "generated": True, "audio_analysis": {"stale": True}},
    )
    db_session.add(old)
    await db_session.commit()
    old_id = str(old.id)

    p1, p2, p3 = _patches()
    with p1, p2, p3:
        resp = await _put(client, auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replaced"] is True
    assert body["asset_id"] == old_id

    assets = (await db_session.execute(select(Asset))).scalars().all()
    assert len(assets) == 1
    await db_session.refresh(assets[0])
    assert "shprach" in assets[0].file_path
    assert assets[0].duration == 44.8
    assert assets[0].metadata_extra["generated"] is False
    assert "audio_analysis" not in assets[0].metadata_extra


@pytest.mark.asyncio
async def test_uploaded_spot_is_used_instead_of_elevenlabs(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    from app.services.weather_spot_service import get_or_create_weather_spot_assets

    p1, p2, p3 = _patches()
    with p1, p2, p3:
        resp = await _put(client, auth_headers)
        assert resp.status_code == 200, resp.text
        tts = AsyncMock(return_value=(b"tts", 3.0))
        with patch("app.services.weather_spot_service.generate_tts", tts), \
             patch("app.services.weather_spot_service.get_current_weather", new_callable=AsyncMock) as weather:
            time_asset, weather_asset = await get_or_create_weather_spot_assets(db_session, f"{DAY}T09")
    assert str(weather_asset.id) == resp.json()["asset_id"]
    weather.assert_not_called()          # no forecast fetched, so no ElevenLabs weather read
    assert tts.await_count == 1          # only the hourly time announcement
    assert time_asset is not None


@pytest.mark.asyncio
async def test_other_days_are_untouched(client: AsyncClient, auth_headers: dict):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        assert (await _put(client, auth_headers)).status_code == 200
    resp = await client.get("/api/v1/weather-spots/2026-11-17", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rejects_unreadable_audio(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    p1, p2, p3 = _patches(duration=0.0)
    with p1, p2, p3 as upload:
        resp = await _put(client, auth_headers)
    assert resp.status_code == 422
    upload.assert_not_called()
    assert (await db_session.execute(select(Asset))).scalars().all() == []


@pytest.mark.asyncio
async def test_requires_login_and_valid_date(client: AsyncClient, auth_headers: dict):
    resp = await client.put(
        f"/api/v1/weather-spots/{DAY}",
        files={"file": ("wk.mp3", io.BytesIO(b"x"), "audio/mpeg")},
    )
    assert resp.status_code in (401, 403)
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        assert (await _put(client, auth_headers, day="not-a-date")).status_code == 422
        assert (await _put(client, auth_headers, source="Bad Source!")).status_code == 422
