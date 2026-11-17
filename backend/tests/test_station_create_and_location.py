"""Station create / update settings: blackouts and silence on create, time zone from
coordinates, duplicate names, list paging and city search.

V1-85, V1-86, V1-87, V1-90, V2-128 (docs/test-checklist.md).
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.station import Station

JERUSALEM = {"latitude": 31.7683, "longitude": 35.2137}
NEW_YORK = {"latitude": 40.7128, "longitude": -74.0060}


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


async def _windows_for(db: AsyncSession, station_id) -> list[HolidayWindow]:
    rows = (await db.execute(select(HolidayWindow))).scalars().all()
    return [w for w in rows if str(station_id) in ((w.affected_stations or {}).get("station_ids") or [])]


@pytest.mark.asyncio
async def test_create_with_location_makes_13_months_of_blackouts_and_silence(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """V1-85"""
    r = await client.post("/api/v1/stations", json={"name": "Kol B'ramah Jerusalem", **JERUSALEM},
                          headers=auth_headers)
    assert r.status_code == 201, r.text
    station_id = r.json()["id"]
    windows = await _windows_for(db_session, station_id)
    assert len(windows) > 50  # a Shabbos every week for a year, plus Yom Tov
    assert all(w.is_blackout for w in windows)
    last_start = max(_naive_utc(w.start_datetime) for w in windows)
    assert last_start > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=360), last_start
    import shutil

    from app.config import settings

    silence = (await db_session.execute(select(Asset).where(Asset.asset_type == "silence"))).scalars().all()
    if not shutil.which(settings.FFMPEG_PATH):  # CI has no ffmpeg: no silence, station still made
        assert silence == []
        return
    assert len(silence) == 1 and silence[0].duration == 300.0

    # a second located station reuses the one silence asset
    r = await client.post("/api/v1/stations", json={"name": "Kol B'ramah NY", **NEW_YORK}, headers=auth_headers)
    assert r.status_code == 201
    assert len((await db_session.execute(select(Asset).where(Asset.asset_type == "silence"))).scalars().all()) == 1
    # a station with no location gets no blackouts
    r = await client.post("/api/v1/stations", json={"name": "No location"}, headers=auth_headers)
    assert r.status_code == 201
    assert await _windows_for(db_session, r.json()["id"]) == []


@pytest.mark.asyncio
async def test_blackout_failure_is_logged_and_station_still_created(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """V1-85: a failure while generating blackouts is logged, the station is still created."""
    from app.services import station_service

    async def boom(db, station, months_ahead=13):
        raise RuntimeError("calendar service down")

    warnings = []
    monkeypatch.setattr(station_service, "auto_generate_holidays_for_station", boom)
    monkeypatch.setattr(station_service.logger, "warning", lambda msg, *a: warnings.append(msg % a))
    r = await client.post("/api/v1/stations", json={"name": "Survives", **JERUSALEM}, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert any("Holiday auto-generation failed" in m and "calendar service down" in m for m in warnings)
    assert (await db_session.execute(select(Station).where(Station.name == "Survives"))).scalar_one()


@pytest.mark.asyncio
async def test_coordinates_override_the_chosen_timezone(client: AsyncClient, auth_headers: dict):
    """V1-86: lat/lon on create or update re-derives the time zone, even over one the user chose.
    Changing other fields leaves the time zone alone."""
    r = await client.post("/api/v1/stations", json={"name": "TZ", "timezone": "Europe/London", **NEW_YORK},
                          headers=auth_headers)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["timezone"] == "America/New_York"

    r = await client.put(f"/api/v1/stations/{sid}", json={"timezone": "Europe/London"}, headers=auth_headers)
    assert r.json()["timezone"] == "Europe/London"  # no coordinates in the request: the choice stands
    r = await client.put(f"/api/v1/stations/{sid}", json={"description": "x"}, headers=auth_headers)
    assert r.json()["timezone"] == "Europe/London"

    # sending coordinates (even with a chosen time zone) re-derives it
    r = await client.put(f"/api/v1/stations/{sid}", json={"timezone": "Europe/London", **JERUSALEM},
                         headers=auth_headers)
    assert r.json()["timezone"] == "Asia/Jerusalem"
    r = await client.put(f"/api/v1/stations/{sid}", json={"latitude": 40.7128}, headers=auth_headers)
    from app.services.timezone_service import get_timezone_for_coords

    # one coordinate sent: the stored other one is used with it
    assert r.json()["timezone"] == get_timezone_for_coords(40.7128, JERUSALEM["longitude"]) == "Europe/Istanbul"


@pytest.mark.asyncio
async def test_duplicate_station_name_is_409(client: AsyncClient, auth_headers: dict):
    """V1-87"""
    assert (await client.post("/api/v1/stations", json={"name": "Twin"}, headers=auth_headers)).status_code == 201
    r = await client.post("/api/v1/stations", json={"name": "Twin"}, headers=auth_headers)
    assert r.status_code == 409
    assert "already exists" in r.text


@pytest.mark.asyncio
async def test_station_list_pages_reach_the_51st_station(client: AsyncClient, db_session: AsyncSession):
    """V1-90: GET /stations returns 50 by default with the full total; the 51st is on the next
    page (skip=50). The frontend (api/stations.ts listAllStations) and infra/streaming/setup.sh
    read page by page with limit 100 until total, so they see every station."""
    db_session.add_all([Station(id=uuid.uuid4(), name=f"S{i:03d}", timezone="UTC") for i in range(51)])
    await db_session.commit()
    r = await client.get("/api/v1/stations")
    assert r.status_code == 200
    assert len(r.json()["stations"]) == 50 and r.json()["total"] == 51
    r = await client.get("/api/v1/stations", params={"skip": 50, "limit": 100})
    assert [s["name"] for s in r.json()["stations"]] == ["S050"]
    r = await client.get("/api/v1/stations", params={"limit": 100})
    assert len(r.json()["stations"]) == 51
    assert (await client.get("/api/v1/stations", params={"limit": 101})).status_code == 422


def test_frontend_and_setup_script_read_every_page():
    """V1-90: the two callers that need every station page through the list."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    api = (root / "frontend" / "src" / "api" / "stations.ts").read_text()
    assert "listStations(stations.length, 100)" in api
    setup = (root / "infra" / "streaming" / "setup.sh").read_text()
    assert "skip={skip}&limit={page_size}" in setup


class _FakeClient:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        return self.behaviour(url, **kw)


@pytest.mark.asyncio
async def test_city_search(client: AsyncClient, auth_headers: dict, monkeypatch):
    """V2-128: Nominatim down or slow -> [] (the screen shows it as "no city found"); a found
    city carries the time zone of its coordinates; a point with no zone at all falls back to UTC."""
    from app.api.v1 import stations as stations_api

    def slow(url, **kw):
        raise httpx.ReadTimeout("took longer than 5 s")

    monkeypatch.setattr(stations_api.httpx, "AsyncClient", _FakeClient(slow))
    r = await client.get("/api/v1/stations/geocode", params={"q": "Lakewood"}, headers=auth_headers)
    assert r.status_code == 200 and r.json() == []

    def down(url, **kw):
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(stations_api.httpx, "AsyncClient", _FakeClient(down))
    r = await client.get("/api/v1/stations/geocode", params={"q": "Lakewood"}, headers=auth_headers)
    assert r.json() == []

    def found(url, **kw):
        body = [{"lat": "40.0821", "lon": "-74.2097", "name": "Lakewood",
                 "address": {"town": "Lakewood", "state": "New Jersey", "country": "United States"}},
                {"lat": "0", "lon": "-30", "name": "Mid-Atlantic", "address": {}}]
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(stations_api.httpx, "AsyncClient", _FakeClient(found))
    r = await client.get("/api/v1/stations/geocode", params={"q": "Lakewood"}, headers=auth_headers)
    first, sea = r.json()
    assert first["display_name"] == "Lakewood, New Jersey, United States"
    assert first["timezone"] == "America/New_York"
    assert sea["timezone"] == "Etc/GMT+2"  # open sea: a fixed-offset zone, not UTC

    from app.services import timezone_service

    class NoZone:
        def timezone_at(self, **kw):
            return None

    monkeypatch.setattr(timezone_service, "_tf", NoZone())
    assert timezone_service.get_timezone_for_coords(0, 0) == "UTC"
    # managers only
    assert (await client.get("/api/v1/stations/geocode", params={"q": "Lakewood"})).status_code in (401, 403)
