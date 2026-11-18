"""Station settings saved through the API reach the air at the next queue refill:
time zone, location, automation_config (weather region, spot timing, long items),
and what happens to the hourly time checks when storage is not set up.

Rows: B-STATIONS-3, B-STATIONS-11, B-STATIONS-12, B-SET-30."""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.services.queue_replenish_service import QueueReplenishService
from app.services.weather_spot_service import clip_hour


@pytest.fixture
def storage_on(monkeypatch):
    """Hourly time checks are only queued with storage configured (B-SET-30)."""
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://storage.invalid")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "test-only")
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "")


async def _hourly_clips(db: AsyncSession, region: str | None = None) -> dict[int, uuid.UUID]:
    """A full set of uploaded "the time is" clips, one per hour."""
    ids = {}
    for hour in range(24):
        meta = {"time_of_day": f"{hour:02d}"}
        if region:
            meta["region"] = region
        a = Asset(id=uuid.uuid4(), title=f"Time {hour:02d} {region or ''}", duration=5.0, asset_type="jingle",
                  category="time_announcement", file_path=f"https://cdn.example.com/t{region}{hour}.mp3",
                  metadata_extra=meta)
        db.add(a)
        ids[hour] = a.id
    db.add(Asset(id=uuid.uuid4(), title="Filler", duration=200.0, asset_type="music",
                 file_path="https://cdn.example.com/f.mp3"))
    await db.commit()
    return ids


async def _create(client: AsyncClient, headers: dict, **body) -> dict:
    r = await client.post("/api/v1/stations", json={"name": f"St {uuid.uuid4().hex[:6]}", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _time_checks(db: AsyncSession, station_id) -> list[tuple[datetime, Asset, QueueEntry]]:
    rows = (await db.execute(
        select(QueueEntry, Asset).join(Asset, Asset.id == QueueEntry.asset_id).where(
            QueueEntry.station_id == uuid.UUID(str(station_id)),
            QueueEntry.status == "pending",
            Asset.category == "time_announcement",
        ).execution_options(populate_existing=True)
    )).all()
    out = []
    for entry, asset in rows:
        at = entry.preempt_at if entry.preempt_at.tzinfo else entry.preempt_at.replace(tzinfo=UTC)
        out.append((at, asset, entry))
    return sorted(out, key=lambda r: r[0])


async def _refill(db: AsyncSession, station_id) -> None:
    await QueueReplenishService(db, uuid.UUID(str(station_id))).replenish()
    await db.commit()


@pytest.mark.asyncio
async def test_new_station_settings_saved_and_it_goes_on_air(client, auth_headers, db_session, monkeypatch):
    """B-STATIONS-3: time zone, location and automation_config are saved as sent (the zone
    comes from the coordinates), and the new station is filled and started by the scheduler's
    next pass (dead-air recovery through the queue), with nobody pressing Start."""
    from app.services.scheduler_engine import SchedulerEngine

    await _hourly_clips(db_session)
    body = await _create(client, auth_headers, timezone="UTC", latitude=31.778, longitude=35.235,
                         automation_config={"weather_region": "jerusalem", "spot_timing": {"other": "hard"}})
    got = (await client.get(f"/api/v1/stations/{body['id']}")).json()
    assert got["timezone"] == "Asia/Jerusalem"                 # derived from the coordinates
    assert (got["latitude"], got["longitude"]) == (31.778, 35.235)
    assert got["automation_config"] == {"weather_region": "jerusalem", "spot_timing": {"other": "hard"}}

    station_id = uuid.UUID(got["id"])
    from app.models.station import Station
    station = await db_session.get(Station, station_id)
    # The new station got its Shabbos windows; run on a Shabbos, the blackout (rightly)
    # keeps the silence timer from starting. This test is about going on air.
    from sqlalchemy import delete

    from app.models.holiday_window import HolidayWindow
    await db_session.execute(delete(HolidayWindow))
    await db_session.commit()
    engine = SchedulerEngine()
    await engine._check_station(db_session, station)                     # first pass: silence timer starts
    engine._silence_start[str(station_id)] -= timedelta(seconds=settings.SILENCE_DETECTION_SECONDS + 1)
    station = await db_session.get(Station, station_id)
    await engine._check_station(db_session, station)                     # threshold passed: queue filled and started
    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id)
    )).scalars().all()
    assert any(r.status == "playing" for r in rows), [(r.status, r.asset_id) for r in rows]


@pytest.mark.asyncio
async def test_timezone_change_moves_time_checks_to_the_new_local_hour(client, auth_headers, db_session, storage_on):
    """B-STATIONS-12: a station moved from New York's clock to Jerusalem's: at the next refill
    every queued time check says Jerusalem's hour at the moment it plays
    (_drop_wrong_hour_announcements replaces the New York ones)."""
    await _hourly_clips(db_session)
    st = await _create(client, auth_headers, timezone="America/New_York",
                       automation_config={"hourly_time_announcement": True})
    await _refill(db_session, st["id"])
    ny = ZoneInfo("America/New_York")
    before = await _time_checks(db_session, st["id"])
    assert len(before) >= 47
    assert all(clip_hour(a) == at.astimezone(ny).hour for at, a, _ in before)

    r = await client.put(f"/api/v1/stations/{st['id']}", json={"timezone": "Asia/Jerusalem"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["timezone"] == "Asia/Jerusalem"
    await _refill(db_session, st["id"])

    jm = ZoneInfo("Asia/Jerusalem")
    after = await _time_checks(db_session, st["id"])
    assert len(after) >= 47
    wrong = [(at.isoformat(), clip_hour(a)) for at, a, _ in after if clip_hour(a) != at.astimezone(jm).hour]
    assert wrong == []
    # One time check per hour, not the old one beside the new one
    assert len({at for at, _, _ in after}) == len(after)


@pytest.mark.asyncio
async def test_automation_config_change_takes_effect_at_next_refill(client, auth_headers, db_session, storage_on):
    """B-STATIONS-11: weather_region and spot_timing changed in the station settings are used
    by the next refill (queued time checks move to the region's clips, undecided timed items
    take the station's new timing); timed_during_long_items is read fresh on each advance."""
    from app.api.v1.queue import _long_item_policy

    default = await _hourly_clips(db_session)
    jerusalem = await _hourly_clips(db_session, "jerusalem")
    st = await _create(client, auth_headers, timezone="America/New_York",
                       automation_config={"hourly_time_announcement": True})
    await _refill(db_session, st["id"])
    before = await _time_checks(db_session, st["id"])
    assert before and all(a.id in default.values() for _, a, _ in before)
    # An item queued before timing modes existed: the station default decides it
    first_at = before[0][0]
    assert first_at.minute == 0
    before[0][2].timing_mode = None
    await db_session.commit()

    cfg = {"hourly_time_announcement": True, "weather_region": "jerusalem",
           "spot_timing": {"on_the_hour": "soft"}, "timed_during_long_items": "cut"}
    r = await client.put(f"/api/v1/stations/{st['id']}", json={"automation_config": cfg}, headers=auth_headers)
    assert r.status_code == 200, r.text
    await _refill(db_session, st["id"])

    after = await _time_checks(db_session, st["id"])
    assert after and all(a.id in jerusalem.values() for _, a, _ in after), "time checks still on the old region"
    undecided = [e for at, _, e in after if at == first_at]
    assert undecided and undecided[0].timing_mode == "soft"

    shiur = Asset(id=uuid.uuid4(), title="Shiur", duration=3600.0, asset_type="shiur",
                  file_path="https://cdn.example.com/s.mp3")
    db_session.add(shiur)
    await db_session.flush()
    on_air = QueueEntry(id=uuid.uuid4(), station_id=uuid.UUID(st["id"]), asset_id=shiur.id, position=0,
                        status="playing", started_at=datetime.now(UTC))
    db_session.add(on_air)
    await db_session.commit()
    await db_session.refresh(on_air, ["asset"])
    assert await _long_item_policy(db_session, uuid.UUID(st["id"]), on_air) == "cut"
    cfg["timed_during_long_items"] = "wait"
    await client.put(f"/api/v1/stations/{st['id']}", json={"automation_config": cfg}, headers=auth_headers)
    assert await _long_item_policy(db_session, uuid.UUID(st["id"]), on_air) == "wait"


@pytest.mark.asyncio
async def test_no_storage_skips_hourly_time_checks(client, auth_headers, db_session, monkeypatch, caplog):
    """B-SET-30 (first half): SUPABASE_URL / SUPABASE_SERVICE_KEY unset -> the refill queues no
    hourly time checks, even with the clips in the library, and says so in the log."""
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "")
    await _hourly_clips(db_session)
    st = await _create(client, auth_headers, timezone="America/New_York",
                       automation_config={"hourly_time_announcement": True})
    with caplog.at_level(logging.INFO, logger="app.services.queue_replenish_service"):
        await _refill(db_session, st["id"])
    assert await _time_checks(db_session, st["id"]) == []
    assert "Skipping hourly announcements: storage not configured" in caplog.text


@pytest.mark.asyncio
async def test_no_storage_keeps_uploads_on_local_disk(monkeypatch, tmp_path):
    """B-SET-30 (second half): with neither S3 nor Supabase configured, uploads are written
    under the local uploads folder and read back and deleted from there."""
    from app.services import storage_service

    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "")
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))

    key = storage_service.generate_asset_key("song.mp3")
    assert await storage_service.upload_file(b"ID3 audio", key) == key
    assert (tmp_path / key).read_bytes() == b"ID3 audio"
    assert await storage_service.download_file(key) == b"ID3 audio"
    await storage_service.delete_file(key)
    assert not (tmp_path / key).exists()
