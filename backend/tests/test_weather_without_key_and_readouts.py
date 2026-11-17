"""Weather with no OpenWeatherMap key, the announcement generator resuming after its
pause, and the daily weather readout (generated at the station's local time, queued at
its queue time, read and previewed on its page).

Rows: B-SET-29, B-JOB-62, B-JOB-48, B-JOB-49, B-SET-17, B-WEATHERREA-1, B-WEATHERREA-3,
B-WEATHERREA-4."""
import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.weather_readout import WeatherReadout
from app.services import weather_service, weather_spot_service

SAMPLE = {"temp_f": 61, "description": "light rain", "wind_speed_mph": 7, "wind_direction": "west",
          "humidity": 80, "forecast": [{"day": "Wednesday", "description": "sunny", "high": 64, "low": 50}]}


@pytest.fixture
def weather_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENWEATHERMAP_API_KEY", "test-only")


def _zone_with_room() -> ZoneInfo:
    """A zone whose local hour now is between 02 and 21, so 'two hours from now' is still today."""
    for name in ("America/New_York", "Asia/Jerusalem", "Asia/Tokyo", "Europe/London", "America/Los_Angeles",
                 "Australia/Sydney", "Asia/Kolkata"):
        zone = ZoneInfo(name)
        if 2 <= datetime.now(UTC).astimezone(zone).hour <= 21:
            return zone
    raise AssertionError("no zone found")


async def _station(db, zone="America/New_York", **readout) -> Station:
    st = Station(id=uuid.uuid4(), name=f"W{uuid.uuid4().hex[:5]}", timezone=str(zone), is_active=True,
                 latitude=40.08, longitude=-74.21, automation_config={"weather_readout": readout} if readout else {})
    db.add(st)
    await db.commit()
    return st


# ── B-SET-29 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_weather_key_never_calls_the_api(monkeypatch):
    """B-SET-29: OPENWEATHERMAP_API_KEY unset -> get_current_weather says weather is not set up
    and makes no request. FIXED: it called the API with an empty key every time (401, then an
    hour's back-off for the location)."""
    monkeypatch.setattr(settings, "OPENWEATHERMAP_API_KEY", "")
    fetch = AsyncMock(return_value=SAMPLE)
    with patch.object(weather_service, "_fetch_weather", fetch):
        with pytest.raises(weather_service.WeatherNotConfiguredError):
            await weather_service.get_current_weather()
    assert fetch.await_count == 0


@pytest.mark.asyncio
async def test_no_weather_key_does_not_stop_time_announcements(db_session, monkeypatch):
    """B-SET-29: with ElevenLabs set up but no weather key, every hour still gets its generated
    time announcement and no weather is attempted. FIXED: the weather attempt failed for the
    first hour and its failure paused all generation, so the other 47 hours had no time check."""
    monkeypatch.setattr(settings, "OPENWEATHERMAP_API_KEY", "")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID", "v")
    tts = AsyncMock(return_value=(b"mp3", 4.0))
    with patch.object(weather_spot_service, "generate_tts", tts), \
         patch.object(weather_spot_service, "upload_to_supabase", AsyncMock(side_effect=lambda d, p: f"https://s/{p}")), \
         patch("app.services.audio_analysis_service.normalize_audio_loudness", side_effect=lambda b: b):
        got = [await weather_spot_service.get_or_create_weather_spot_assets(db_session, f"2026-11-18T{h:02d}")
               for h in range(6)]
    assert all(t is not None for t, _ in got)
    assert all(w is None for _, w in got)
    assert tts.await_count == 6


@pytest.mark.asyncio
async def test_readout_pages_say_weather_is_not_set_up(client, auth_headers, db_session, monkeypatch):
    """B-SET-29 / B-WEATHERREA-3: template preview and making a readout with no weather key ->
    503 that says so. FIXED: both were an unexplained server error."""
    monkeypatch.setattr(settings, "OPENWEATHERMAP_API_KEY", "")
    st = await _station(db_session)
    r = await client.get("/api/v1/weather-readouts/template-preview", params={"station_id": str(st.id)},
                         headers=auth_headers)
    assert r.status_code == 503 and "not set up" in r.json()["detail"]
    r = await client.post("/api/v1/weather-readouts/", json={"station_id": str(st.id)}, headers=auth_headers)
    assert r.status_code == 503


# ── B-JOB-62 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generation_resumes_after_the_pause(db_session, monkeypatch, weather_key):
    """B-JOB-62: an ElevenLabs failure pauses generation for GENERATION_PAUSE_SECONDS; once that
    has passed the next slot is generated again."""
    import time as time_module

    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(settings, "ELEVENLABS_VOICE_ID", "v")
    tts = AsyncMock(side_effect=[RuntimeError("503"), (b"mp3", 4.0), (b"mp3", 20.0)])
    clock = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: clock[0])
    with patch.object(weather_spot_service, "generate_tts", tts), \
         patch.object(weather_spot_service, "get_current_weather", AsyncMock(return_value=SAMPLE)), \
         patch.object(weather_spot_service, "upload_to_supabase", AsyncMock(side_effect=lambda d, p: f"https://s/{p}")), \
         patch("app.services.audio_analysis_service.normalize_audio_loudness", side_effect=lambda b: b):
        assert await weather_spot_service.get_or_create_weather_spot_assets(db_session, "2026-11-18T08") == (None, None)
        clock[0] += weather_spot_service.GENERATION_PAUSE_SECONDS - 1
        assert await weather_spot_service.get_or_create_weather_spot_assets(db_session, "2026-11-18T09") == (None, None)
        assert tts.await_count == 1                                     # still paused
        clock[0] += 2
        t, w = await weather_spot_service.get_or_create_weather_spot_assets(db_session, "2026-11-18T09")
    assert t is not None and w is not None and tts.await_count == 3


# ── B-JOB-48 / B-SET-17 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_readout_made_at_the_stations_generate_time(db_session, weather_key):
    """B-JOB-48 / B-SET-17: a station with weather_readout.enabled gets one readout a day, once
    its local time passes generate_time, rendered from its template, city and brand, with its
    queue_time; before generate_time, or when not enabled, nothing is made."""
    from app.services.scheduler_engine import SchedulerEngine

    zone = _zone_with_room()
    later = (datetime.now(UTC).astimezone(zone) + timedelta(hours=2)).strftime("%H:%M")
    due = await _station(db_session, zone, enabled=True, generate_time="00:00", queue_time="07:30",
                         template="{city} {temp_f} {description} {brand_name} {day1_name}",
                         city_name="Monsey", brand_name="KBR")
    not_yet = await _station(db_session, zone, enabled=True, generate_time=later)
    off = await _station(db_session, zone, enabled=False, generate_time="00:00")

    engine = SchedulerEngine()
    with patch.object(weather_service, "_fetch_weather", AsyncMock(return_value=SAMPLE)):
        await engine._maybe_generate_weather_readouts(db_session)
        await engine._maybe_generate_weather_readouts(db_session)        # same day: nothing more
    rows = (await db_session.execute(select(WeatherReadout))).scalars().all()
    assert [r.station_id for r in rows] == [due.id]
    r = rows[0]
    assert r.script_text == "Monsey 61 light rain KBR Wednesday"
    assert r.readout_date == datetime.now(UTC).astimezone(zone).date()
    assert r.queue_time == time(7, 30) and r.status == "pending" and r.generated_by == "auto"
    assert not_yet.id and off.id


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=(
    "BUG: when the weather fetch fails at generate_time the scheduler still marks the station "
    "as checked for the day, so no readout is made that day even after the weather service is back"))
async def test_failed_readout_is_tried_again_the_same_day(db_session, weather_key):
    """B-JOB-48: a readout that could not be made (weather down) is tried again on a later pass."""
    from app.services.scheduler_engine import SchedulerEngine

    st = await _station(db_session, _zone_with_room(), enabled=True, generate_time="00:00")
    engine = SchedulerEngine()
    with patch("app.services.weather_readout_service.get_current_weather", AsyncMock(side_effect=RuntimeError("down"))):
        await engine._maybe_generate_weather_readouts(db_session)
    with patch("app.services.weather_readout_service.get_current_weather", AsyncMock(return_value=SAMPLE)):
        await engine._maybe_generate_weather_readouts(db_session)
    rows = (await db_session.execute(select(WeatherReadout).where(WeatherReadout.station_id == st.id))).scalars().all()
    assert len(rows) == 1


# ── B-JOB-49 ──────────────────────────────────────────────────────────────


async def _recorded(db, station, *, day: date, at: time) -> WeatherReadout:
    audio = Asset(id=uuid.uuid4(), title="Readout", duration=40.0, asset_type="spot",
                  file_path="https://cdn.example.com/r.mp3")
    db.add(audio)
    await db.flush()
    r = WeatherReadout(id=uuid.uuid4(), station_id=station.id, readout_date=day, script_text="x",
                       status="recorded", asset_id=audio.id, queue_time=at)
    db.add(r)
    await db.commit()
    return r


@pytest.mark.asyncio
async def test_recorded_readout_queued_at_its_time_when_auto_queue(db_session):
    """B-JOB-49: a recorded readout is queued (next in line) once the station's local time reaches
    its queue_time, only when weather_readout.auto_queue is on, and only once."""
    zone = _zone_with_room()
    today = datetime.now(UTC).astimezone(zone).date()
    later = (datetime.now(UTC).astimezone(zone) + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    auto = await _station(db_session, zone, enabled=True, auto_queue=True)
    manual = await _station(db_session, zone, enabled=True, auto_queue=False)
    r_due = await _recorded(db_session, auto, day=today, at=time(0, 0))
    r_later = await _recorded(db_session, auto, day=today, at=later)
    r_manual = await _recorded(db_session, manual, day=today, at=time(0, 0))

    from app.services.scheduler_engine import SchedulerEngine

    engine = SchedulerEngine()
    await engine._maybe_queue_weather_readouts(db_session)
    await engine._maybe_queue_weather_readouts(db_session)
    queued = (await db_session.execute(select(QueueEntry))).scalars().all()
    assert [(q.station_id, q.asset_id) for q in queued] == [(auto.id, r_due.asset_id)]
    for r, status in ((r_due, "queued"), (r_later, "recorded"), (r_manual, "recorded")):
        await db_session.refresh(r)
        assert r.status == status


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=(
    "BUG: auto-queue compares only the time of day, not readout_date: tomorrow's readout, "
    "recorded the evening before, is queued at once and tomorrow's weather plays tonight"))
async def test_tomorrows_readout_waits_for_tomorrow(db_session):
    """B-JOB-49: a readout recorded for tomorrow is not queued today."""
    from app.services.scheduler_engine import SchedulerEngine

    zone = _zone_with_room()
    tomorrow = datetime.now(UTC).astimezone(zone).date() + timedelta(days=1)
    st = await _station(db_session, zone, enabled=True, auto_queue=True)
    await _recorded(db_session, st, day=tomorrow, at=time(0, 0))
    await SchedulerEngine()._maybe_queue_weather_readouts(db_session)
    assert (await db_session.execute(select(QueueEntry))).scalars().all() == []


# ── B-WEATHERREA-1 / 3 / 4 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readout_list_get_and_template_preview(client, auth_headers, db_session, weather_key):
    """B-WEATHERREA-1 / B-WEATHERREA-3 / B-WEATHERREA-4: readouts listed per station, one read
    by id (unknown -> 404), and the template preview renders the station's own template with
    live weather (unknown station -> 404)."""
    a = await _station(db_session, template="It is {temp_f} in {city}", city_name="Lakewood", enabled=True)
    b = await _station(db_session)
    for st, d in ((a, date(2026, 11, 17)), (a, date(2026, 11, 18)), (b, date(2026, 11, 18))):
        db_session.add(WeatherReadout(id=uuid.uuid4(), station_id=st.id, readout_date=d, script_text=f"{d}"))
    await db_session.commit()

    r = await client.get("/api/v1/weather-readouts/", params={"station_id": str(a.id)}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and {x["station_id"] for x in body["readouts"]} == {str(a.id)}
    one = body["readouts"][0]
    got = await client.get(f"/api/v1/weather-readouts/{one['id']}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["script_text"] == one["script_text"]
    assert (await client.get(f"/api/v1/weather-readouts/{uuid.uuid4()}", headers=auth_headers)).status_code == 404

    with patch.object(weather_service, "_fetch_weather", AsyncMock(return_value=SAMPLE)):
        p = await client.get("/api/v1/weather-readouts/template-preview", params={"station_id": str(a.id)},
                             headers=auth_headers)
        custom = await client.get("/api/v1/weather-readouts/template-preview",
                                  params={"station_id": str(a.id), "template": "{description}!"}, headers=auth_headers)
        missing = await client.get("/api/v1/weather-readouts/template-preview",
                                   params={"station_id": str(uuid.uuid4())}, headers=auth_headers)
    assert p.status_code == 200 and p.json()["rendered"] == "It is 61 in Lakewood"
    assert custom.json()["rendered"] == "light rain!"
    assert missing.status_code == 404
