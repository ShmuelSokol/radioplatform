"""Each station plays its own city's weather (KBR Jerusalem must not air Lakewood's report)."""
import io
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from tests.test_weather_spots import _patches

DAY = "2026-11-16"


async def _put(client: AsyncClient, headers: dict, region: str | None = None, day: str = DAY):
    return await client.put(
        f"/api/v1/weather-spots/{day}",
        params={"region": region} if region else None,
        files={"file": ("w.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        data={"script": f"weather for {region or 'lakewood'}"},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_regions_keep_separate_spots_for_the_same_day(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        lk = await _put(client, auth_headers)
        jm = await _put(client, auth_headers, "jerusalem")
        jm2 = await _put(client, auth_headers, "Jerusalem")          # case does not matter; replaces in place
    assert lk.status_code == 200 and jm.status_code == 200, (lk.text, jm.text)
    assert lk.json()["region"] == "lakewood" and jm.json()["region"] == "jerusalem"
    assert lk.json()["asset_id"] != jm.json()["asset_id"]
    assert jm2.json()["asset_id"] == jm.json()["asset_id"] and jm2.json()["replaced"] is True
    assert "_jerusalem_weather_" in jm.json()["file_url"]

    got_lk = await client.get(f"/api/v1/weather-spots/{DAY}", headers=auth_headers)
    got_jm = await client.get(f"/api/v1/weather-spots/{DAY}", params={"region": "jerusalem"}, headers=auth_headers)
    assert got_lk.json()["asset_id"] == lk.json()["asset_id"]
    assert got_jm.json()["asset_id"] == jm.json()["asset_id"]
    assert (await client.get(f"/api/v1/weather-spots/{DAY}", params={"region": "haifa"}, headers=auth_headers)).status_code == 404

    titles = sorted(a.title for a in (await db_session.execute(select(Asset))).scalars())
    assert titles == [f"Weather Report - {DAY}", f"Weather Report - {DAY} (Jerusalem)"]


@pytest.mark.asyncio
async def test_old_spots_without_region_belong_to_the_default_region(db_session: AsyncSession):
    from app.services.weather_spot_service import find_weather_spot_asset

    old = Asset(id=uuid.uuid4(), title=f"Weather Report - {DAY}", file_path="x", asset_type="spot",
                category="weather_spot", metadata_extra={"slot_key": DAY, "generated": True})
    db_session.add(old)
    await db_session.commit()
    assert (await find_weather_spot_asset(db_session, DAY)).id == old.id
    assert (await find_weather_spot_asset(db_session, DAY, "lakewood")).id == old.id
    assert await find_weather_spot_asset(db_session, DAY, "jerusalem") is None


@pytest.mark.asyncio
async def test_other_region_never_falls_back_or_generates(db_session: AsyncSession):
    """No Jerusalem spot: Jerusalem gets no weather (not Lakewood's, and no ElevenLabs read of Lakewood's forecast)."""
    from app.services.weather_spot_service import get_or_create_weather_spot_assets

    db_session.add(Asset(id=uuid.uuid4(), title=f"Weather Report - {DAY}", file_path="x", asset_type="spot",
                         category="weather_spot", metadata_extra={"slot_key": DAY}))
    await db_session.commit()
    tts = AsyncMock(return_value=(b"tts", 3.0))
    weather = AsyncMock()
    p1, p2, p3 = _patches()
    with p1, p2, p3, \
         patch("app.services.weather_spot_service.settings.ELEVENLABS_API_KEY", "k"), \
         patch("app.services.weather_spot_service.settings.ELEVENLABS_VOICE_ID", "v"), \
         patch("app.services.weather_spot_service.generate_tts", tts), \
         patch("app.services.weather_spot_service.get_current_weather", weather):
        _, jm = await get_or_create_weather_spot_assets(db_session, f"{DAY}T09", "jerusalem")
        _, lk = await get_or_create_weather_spot_assets(db_session, f"{DAY}T09")
    assert jm is None
    assert lk is not None
    weather.assert_not_called()


@pytest.mark.asyncio
async def test_queued_weather_moves_to_the_stations_own_region(db_session: AsyncSession):
    from app.services.queue_replenish_service import QueueReplenishService

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem",
                      automation_config={"weather_enabled": True, "weather_region": "jerusalem"})
    lakewood = Asset(id=uuid.uuid4(), title=f"Weather Report - {DAY}", file_path="lk", asset_type="spot",
                     category="weather_spot", metadata_extra={"slot_key": DAY})
    other_day = Asset(id=uuid.uuid4(), title="Weather Report - 2026-11-17", file_path="lk2", asset_type="spot",
                      category="weather_spot", metadata_extra={"slot_key": "2026-11-17"})
    jerusalem = Asset(id=uuid.uuid4(), title=f"Weather Report - {DAY} (Jerusalem)", file_path="jm", asset_type="spot",
                      category="weather_spot", metadata_extra={"slot_key": DAY, "region": "jerusalem"})
    db_session.add_all([station, lakewood, other_day, jerusalem])
    at = datetime.now(timezone.utc) + timedelta(hours=1)
    e1 = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=lakewood.id, position=1, status="pending", preempt_at=at)
    e2 = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=other_day.id, position=2, status="pending", preempt_at=at)
    db_session.add_all([e1, e2])
    await db_session.commit()

    svc = QueueReplenishService(db_session, station.id)
    changed = await svc._realign_weather_region("jerusalem")
    await db_session.commit()
    assert changed == 2
    left = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == station.id))).scalars().all()
    assert [str(e.asset_id) for e in left] == [str(jerusalem.id)]      # moved; the day with no Jerusalem spot is dropped
    assert await svc._realign_weather_region("jerusalem") == 0          # nothing left to move


def test_station_region_setting():
    from app.services.weather_spot_service import station_weather_region

    assert station_weather_region(None) == "lakewood"
    assert station_weather_region({}) == "lakewood"
    assert station_weather_region({"weather_region": " Jerusalem "}) == "jerusalem"


@pytest.mark.asyncio
async def test_region_must_be_simple(client: AsyncClient, auth_headers: dict):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        assert (await _put(client, auth_headers, "../etc")).status_code == 422
