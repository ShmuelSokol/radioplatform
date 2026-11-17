"""Shabbos / Yom Tov blackouts made from a station's location, the preview that saves
nothing, and the silence item blackouts play.

Rows: B-HOLIDAYS-5, B-HOLIDAYS-7, B-HOLIDAYS-8, B-HOLIDAYS-9."""
import shutil
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.services.sun_service import get_sun_times

LAKEWOOD = (40.0821, -74.2097, "America/New_York")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


async def _station(db, name="Lakewood", where=LAKEWOOD):
    lat, lon, tz = where
    st = Station(id=uuid.uuid4(), name=name, timezone=tz, latitude=lat, longitude=lon, is_active=True)
    db.add(st)
    await db.commit()
    return st


async def _windows(db) -> int:
    return (await db.execute(select(func.count(HolidayWindow.id)))).scalar()


@pytest.mark.asyncio
async def test_preview_lists_windows_and_saves_nothing(client, auth_headers, db_session):
    """B-HOLIDAYS-5: preview returns the Shabbos and Yom Tov windows with their counts and
    lengths and writes no row; a station without a location -> 400, unknown -> 404."""
    st = await _station(db_session)
    r = await client.post("/api/v1/holidays/preview", json={"station_id": str(st.id), "months_ahead": 12},
                          headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["windows"]) == body["shabbos_count"] + body["yom_tov_count"]
    assert 50 <= body["shabbos_count"] <= 56 and body["yom_tov_count"] >= 1
    for w in body["windows"]:
        start, end = datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])
        assert w["duration_hours"] == round((end - start).total_seconds() / 3600, 2)
        assert 24 < w["duration_hours"] < 24 * 4         # a Shabbos, or Yom Tov running into Shabbos
    assert await _windows(db_session) == 0

    no_place = Station(id=uuid.uuid4(), name="Nowhere", timezone="UTC", is_active=True)
    db_session.add(no_place)
    await db_session.commit()
    r = await client.post("/api/v1/holidays/preview", json={"station_id": str(no_place.id)}, headers=auth_headers)
    assert r.status_code == 400
    r = await client.post("/api/v1/holidays/preview", json={"station_id": str(uuid.uuid4())}, headers=auth_headers)
    assert r.status_code == 404
    r = await client.post("/api/v1/holidays/preview", json={"station_id": str(st.id), "months_ahead": 30},
                          headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_auto_generate_uses_the_stations_own_sunset(client, auth_headers, db_session):
    """B-HOLIDAYS-7: every Shabbos window runs from 18 min before Friday's sunset to 72 min
    after Saturday's, at the station's own location and time zone; running it again adds
    nothing; Yom Kippur is there."""
    st = await _station(db_session)
    r = await client.post("/api/v1/holidays/auto-generate", json={"station_id": str(st.id), "months_ahead": 3},
                          headers=auth_headers)
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert created >= 12 and r.json()["skipped"] == 0

    rows = (await db_session.execute(select(HolidayWindow))).scalars().all()
    assert all(w.is_blackout and w.affected_stations == {"station_ids": [str(st.id)]} for w in rows)

    # The times themselves, with their offsets (SQLite drops the zone of stored rows)
    lat, lon, tz = LAKEWOOD
    zone = ZoneInfo(tz)
    shown = (await client.post("/api/v1/holidays/preview", json={"station_id": str(st.id), "months_ahead": 3},
                               headers=auth_headers)).json()["windows"]
    shabbos = [w for w in shown if w["name"].startswith("Shabbos") and "/" not in w["name"]]
    assert len(shabbos) >= 10
    for w in shabbos:
        start, end = datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])
        friday = start.astimezone(zone).date()
        assert friday.weekday() == 4, w["name"]
        fri_sunset = get_sun_times(lat, lon, tz, friday)["sunset"]
        sat_sunset = get_sun_times(lat, lon, tz, friday + timedelta(days=1))["sunset"]
        assert abs((start - (fri_sunset - timedelta(minutes=18))).total_seconds()) < 60, w["name"]
        assert abs((end - (sat_sunset + timedelta(minutes=72))).total_seconds()) < 60, w["name"]
        assert 15 <= start.astimezone(zone).hour <= 20             # Friday afternoon/evening, local

    again = await client.post("/api/v1/holidays/auto-generate", json={"station_id": str(st.id), "months_ahead": 3},
                              headers=auth_headers)
    assert again.json() == {"created": 0, "skipped": created}
    assert await _windows(db_session) == created

    year = await client.post("/api/v1/holidays/preview", json={"station_id": str(st.id), "months_ahead": 12},
                             headers=auth_headers)
    assert any("Yom Kippur" in w["name"] for w in year.json()["windows"])


@pytest.mark.asyncio
async def test_second_station_in_the_same_town_gets_its_own_shabbos(client, auth_headers, db_session):
    """B-HOLIDAYS-7: a second station a few miles away gets its own windows. FIXED: the
    endpoint's duplicate check matched on name and time only, so every window of the second
    station was "skipped" as a duplicate of the first's and it played through Shabbos
    (the scheduler's daily extension already checked the station)."""
    a = await _station(db_session, "Lakewood")
    b = await _station(db_session, "Toms River", (39.9537, -74.1979, "America/New_York"))
    ra = await client.post("/api/v1/holidays/auto-generate", json={"station_id": str(a.id), "months_ahead": 2},
                           headers=auth_headers)
    rb = await client.post("/api/v1/holidays/auto-generate", json={"station_id": str(b.id), "months_ahead": 2},
                           headers=auth_headers)
    assert ra.status_code == 200 and rb.status_code == 200, rb.text
    assert rb.json()["created"] == ra.json()["created"] > 0
    rows = (await db_session.execute(select(HolidayWindow))).scalars().all()
    covers_b = [w for w in rows if str(b.id) in (w.affected_stations or {}).get("station_ids", [])]
    assert len(covers_b) == rb.json()["created"]
    # A third run for either station still adds nothing
    again = await client.post("/api/v1/holidays/auto-generate", json={"station_id": str(b.id), "months_ahead": 2},
                              headers=auth_headers)
    assert again.status_code == 200 and again.json()["created"] == 0


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=(
    "BUG: moving a station (new latitude/longitude) keeps the blackouts made for its old "
    "location; the new town's Shabbos is only added when the daily extension next runs, and "
    "the old windows are never removed, so the station goes silent at the old town's times"))
async def test_moving_a_station_moves_its_shabbos(client, auth_headers, db_session):
    """B-HOLIDAYS-7: windows follow the station's location after it is changed."""
    st = await _station(db_session)
    await client.post("/api/v1/holidays/auto-generate", json={"station_id": str(st.id), "months_ahead": 1},
                      headers=auth_headers)
    old = {w.id for w in (await db_session.execute(select(HolidayWindow))).scalars().all()}
    assert old
    r = await client.put(f"/api/v1/stations/{st.id}", json={"latitude": 31.778, "longitude": 35.235},
                         headers=auth_headers)
    assert r.status_code == 200 and r.json()["timezone"] == "Asia/Jerusalem"
    rows = (await db_session.execute(select(HolidayWindow).execution_options(populate_existing=True))).scalars().all()
    left = [w.name for w in rows if w.id in old and str(st.id) in (w.affected_stations or {}).get("station_ids", [])]
    assert left == []
    assert any(w.id not in old for w in rows)                       # the new town's windows are there


@pytest.mark.asyncio
@needs_ffmpeg
async def test_ensure_silence_asset_makes_it_once(client, auth_headers, db_session, monkeypatch):
    """B-HOLIDAYS-8 / B-HOLIDAYS-9: no silence item -> FFmpeg makes a 5-minute silent MP3,
    uploaded to storage when set up, saved as an approved "silence" item; asking again
    returns the same item without making another."""
    from app.config import settings

    monkeypatch.setattr(settings, "SUPABASE_URL", "https://storage.invalid")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "test-only")
    uploaded = {}

    async def fake_upload(data, path):
        uploaded[path] = data
        return f"https://storage.invalid/{path}"

    with patch("app.services.supabase_storage_service.upload_to_supabase", new=AsyncMock(side_effect=fake_upload)):
        r = await client.post("/api/v1/holidays/ensure-silence-asset", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["already_existed"] is False and body["title"] == "Silence (5 min)"
    assert body["file_path"] == "https://storage.invalid/system/silence_5min.mp3"
    data = uploaded["system/silence_5min.mp3"]
    assert len(data) > 100_000                       # 5 min at 128 kb/s is about 4.8 MB
    asset = await db_session.get(Asset, uuid.UUID(body["id"]))
    assert asset.asset_type == "silence" and asset.duration == 300.0 and asset.review_status == "approved"

    again = await client.post("/api/v1/holidays/ensure-silence-asset", headers=auth_headers)
    assert again.json()["already_existed"] is True and again.json()["id"] == body["id"]
    count = (await db_session.execute(select(func.count(Asset.id)).where(Asset.asset_type == "silence"))).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_ensure_silence_asset_reports_ffmpeg_failure(client, auth_headers, db_session, monkeypatch):
    """B-HOLIDAYS-9: FFmpeg failing -> 500 that says so, and no half-made item."""
    from app.config import settings

    false = shutil.which("false")
    if false is None:
        pytest.skip("no `false` binary")
    monkeypatch.setattr(settings, "FFMPEG_PATH", false)
    r = await client.post("/api/v1/holidays/ensure-silence-asset", headers=auth_headers)
    assert r.status_code == 500 and "FFmpeg failed" in r.json()["detail"]
    count = (await db_session.execute(select(func.count(Asset.id)).where(Asset.asset_type == "silence"))).scalar()
    assert count == 0
