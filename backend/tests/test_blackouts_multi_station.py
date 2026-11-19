"""Blackouts, Shabbos and stations (checklist V1 E): generated Shabbos / Yom Tov windows
for several stations, the windows' times in each station's own zone, station creation
with a location, the station's time zone, the holidays endpoints' refusals, manual
blackouts for some stations, and deleting a library item that a spot schedule or a
program block uses.

Rows: V1-80, V1-81, V1-82, V1-84, V1-85, V1-86, V1-88, V1-89, V1-91."""
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.station_service import auto_generate_holidays_for_station, window_covers_station
from app.services.sun_service import get_sun_times

LAKEWOOD = (40.0821, -74.2097, "America/New_York")
LOS_ANGELES = (34.0901, -118.3617, "America/Los_Angeles")
JERUSALEM = (31.778, 35.235, "Asia/Jerusalem")


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_replenish(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _silence(db):
    a = Asset(id=uuid.uuid4(), title="Silence (5 min)", duration=300.0, asset_type="silence",
              file_path="system/silence_5min.mp3")
    db.add(a)
    await db.commit()
    return a


async def _located(db, name, where):
    lat, lon, tz = where
    st = Station(id=uuid.uuid4(), name=name, timezone=tz, latitude=lat, longitude=lon, is_active=True)
    db.add(st)
    await db.commit()
    return st


async def _covering(db, station_id) -> list[HolidayWindow]:
    rows = (await db.execute(select(HolidayWindow).execution_options(populate_existing=True))).scalars().all()
    return [w for w in rows if window_covers_station(w, station_id)]


# ── V1-80 / V1-81: every station gets its own windows ─────────────────────


@pytest.mark.asyncio
async def test_each_station_gets_its_own_windows_in_its_own_town(db_session):
    """V1-80: stations in different towns and zones each get a full set of their own
    windows; one station's windows do not black out the other, and a window for every
    station (no list) already counts as covering a station"""
    await _silence(db_session)
    east = await _located(db_session, "East", LAKEWOOD)
    west = await _located(db_session, "West", LOS_ANGELES)
    made_east = await auto_generate_holidays_for_station(db_session, east, months_ahead=2)
    made_west = await auto_generate_holidays_for_station(db_session, west, months_ahead=2)
    await db_session.commit()
    assert made_east > 0 and made_west == made_east

    east_rows = await _covering(db_session, east.id)
    west_rows = await _covering(db_session, west.id)
    assert len(east_rows) == made_east and len(west_rows) == made_west
    assert not {w.id for w in east_rows} & {w.id for w in west_rows}   # nothing shared

    # A third station in Lakewood: an all-stations window with the same name and time
    # counts as its own (skipped), the rest are made for it
    third = await _located(db_session, "Third", LAKEWOOD)
    first = min(east_rows, key=lambda w: w.start_datetime)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name=first.name, start_datetime=first.start_datetime,
                                 end_datetime=first.end_datetime, is_blackout=True, affected_stations=None))
    await db_session.commit()
    made_third = await auto_generate_holidays_for_station(db_session, third, months_ahead=2)
    assert made_third == made_east - 1
    assert len(await _covering(db_session, third.id)) == made_east


@pytest.mark.asyncio
async def test_daily_extension_really_fills_a_bare_station(db_session):
    """V1-81: the daily extension, with real generation, adds windows to a station that
    has none while another station is covered for over a year by a window for every
    station (which counts for it too, so that one is left alone)"""
    from app.services.scheduler_engine import SchedulerEngine

    await _silence(db_session)
    covered = await _located(db_session, "Covered", LAKEWOOD)
    bare = await _located(db_session, "Bare", LOS_ANGELES)
    now = datetime.now(UTC)
    for d in range(0, 400, 7):
        db_session.add(HolidayWindow(
            id=uuid.uuid4(), name=f"Only Covered {d}", start_datetime=now + timedelta(days=d),
            end_datetime=now + timedelta(days=d, hours=25), is_blackout=True,
            affected_stations={"station_ids": [str(covered.id)]}))
    await db_session.commit()

    await SchedulerEngine()._maybe_extend_holidays(db_session)
    bare_rows = await _covering(db_session, bare.id)
    covered_rows = await _covering(db_session, covered.id)
    assert len(bare_rows) > 50                                  # about 13 months of Shabbos + Yom Tov
    assert max(_aware(w.end_datetime) for w in bare_rows) - now > timedelta(days=365)
    assert len(covered_rows) == len(range(0, 400, 7))           # untouched
    assert all(w.name.startswith(("Shabbos", "Rosh", "Yom", "Sukkot", "Shemini", "Pesach", "Shavuot"))
               for w in bare_rows)


# ── V1-82 / V1-84: the windows' times ─────────────────────────────────────


@pytest.mark.parametrize("where", [LOS_ANGELES, JERUSALEM], ids=["los-angeles", "jerusalem"])
def test_shabbos_window_is_in_the_stations_own_zone(where):
    """V1-82: candle lighting (Friday sunset - 18 min, 40 in Jerusalem) to Saturday sunset
    + 72 min, on the station's local Friday and Saturday, with the station's zone offset"""
    from app.services.shabbos_service import generate_shabbos_windows

    lat, lon, tz = where
    zone = ZoneInfo(tz)
    friday = date(2027, 1, 15)
    w = generate_shabbos_windows(lat, lon, tz, friday, friday + timedelta(days=1), ["s"])[0]
    start, end = datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])
    fri = get_sun_times(lat, lon, tz, friday)["sunset"]
    sat = get_sun_times(lat, lon, tz, friday + timedelta(days=1))["sunset"]
    candles = 40 if tz == "Asia/Jerusalem" else 18
    assert start == fri - timedelta(minutes=candles) and end == sat + timedelta(minutes=72)
    assert start.utcoffset() == datetime(2027, 1, 15, 12, tzinfo=zone).utcoffset()
    assert start.astimezone(zone).date() == friday and start.astimezone(zone).weekday() == 4
    assert end.astimezone(zone).date() == friday + timedelta(days=1)
    assert 15 <= start.astimezone(zone).hour <= 18                       # a winter Friday afternoon there


def test_yom_tov_runs_from_erev_to_the_last_day():
    """V1-82: Yom Tov from candle lighting on erev (sunset - 18 min, 40 in Jerusalem) to the
    last day (sunset + 72 min):
    two days in Los Angeles, one in Jerusalem"""
    from pyluach.dates import HebrewDate

    from app.services.shabbos_service import generate_yom_tov_windows

    nissan15 = HebrewDate(5787, 1, 15).to_pydate()
    for (lat, lon, tz), days in ((LOS_ANGELES, 2), (JERUSALEM, 1)):
        windows = generate_yom_tov_windows(lat, lon, tz, nissan15 - timedelta(days=3),
                                           nissan15 + timedelta(days=3), ["s"])
        pesach = next(w for w in windows if w["name"].startswith("Pesach (First Days)"))
        erev, last = nissan15 - timedelta(days=1), nissan15 + timedelta(days=days - 1)
        assert datetime.fromisoformat(pesach["start_datetime"]) == \
            get_sun_times(lat, lon, tz, erev)["sunset"] - timedelta(minutes=40 if tz == "Asia/Jerusalem" else 18)
        assert datetime.fromisoformat(pesach["end_datetime"]) == \
            get_sun_times(lat, lon, tz, last)["sunset"] + timedelta(minutes=72)


@pytest.mark.asyncio
async def test_shabbos_into_rosh_hashanah_is_one_stored_blackout(db_session):
    """V1-84: Rosh Hashanah 5787 starts on Shabbos (Sept 12-13, 2026): one window from
    Friday's candle lighting to Sunday night named "Shabbos ... / Rosh Hashanah 5787",
    and the station is blacked out all through Saturday night (no music between)"""
    from pyluach.dates import HebrewDate

    from app.services.scheduler_engine import SchedulerEngine
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    lat, lon, tz = LAKEWOOD
    rh1 = HebrewDate(5787, 7, 1).to_pydate()
    assert rh1.weekday() == 5                                   # Saturday
    friday = rh1 - timedelta(days=1)
    merged = merge_overlapping_windows(
        generate_shabbos_windows(lat, lon, tz, friday - timedelta(days=1), rh1 + timedelta(days=2), ["s"])
        + generate_yom_tov_windows(lat, lon, tz, friday - timedelta(days=1), rh1 + timedelta(days=2), ["s"])
    )
    assert len(merged) == 1
    w = merged[0]
    assert w["name"] == f"Shabbos {friday.strftime('%b %d, %Y')} / Rosh Hashanah 5787"
    assert w["reason"] == "Rosh Hashanah"
    start, end = datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])
    assert start == get_sun_times(lat, lon, tz, friday)["sunset"] - timedelta(minutes=18)
    assert end == get_sun_times(lat, lon, tz, rh1 + timedelta(days=1))["sunset"] + timedelta(minutes=72)

    station = await _located(db_session, "RH FM", LAKEWOOD)
    # Stored in UTC (the SQLite test database drops the zone and keeps the wall time)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name=w["name"], start_datetime=start.astimezone(UTC),
                                 end_datetime=end.astimezone(UTC),
                                 is_blackout=True, affected_stations={"station_ids": [str(station.id)]}))
    await db_session.commit()
    engine = SchedulerEngine()
    saturday_night = get_sun_times(lat, lon, tz, rh1)["sunset"] + timedelta(minutes=90)
    for at in (start + timedelta(minutes=1), saturday_night, end - timedelta(minutes=1)):
        assert await engine._is_station_blacked_out(db_session, station, at.astimezone(UTC))
    assert not await engine._is_station_blacked_out(db_session, station, (end + timedelta(minutes=1)).astimezone(UTC))


# ── V1-85 / V1-86: creating and moving a station ──────────────────────────


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """The silence item is made with FFmpeg; the test only needs it to "succeed"."""
    import asyncio

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    calls = []

    async def fake_exec(*cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


@pytest.mark.asyncio
async def test_new_station_with_a_location_gets_13_months_and_the_silence_item(
    client, auth_headers, db_session, fake_ffmpeg,
):
    """V1-85: creating a station with latitude/longitude generates about 13 months of its
    own Shabbos / Yom Tov blackouts and the 5-minute silence item"""
    r = await client.post("/api/v1/stations", json={"name": "Lakewood New", "latitude": LAKEWOOD[0],
                                                    "longitude": LAKEWOOD[1]}, headers=auth_headers)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    rows = await _covering(db_session, sid)
    rows = [w for w in rows if w.affected_stations]               # its own, not any all-station row
    assert len(rows) > 50
    ends = [_aware(w.end_datetime) for w in rows]
    assert max(ends) - datetime.now(UTC) > timedelta(days=365)
    assert any("Yom Kippur" in w.name for w in rows)
    silence = (await db_session.execute(select(Asset).where(Asset.asset_type == "silence"))).scalars().all()
    assert len(silence) == 1 and silence[0].duration == 300.0 and silence[0].title == "Silence (5 min)"
    assert fake_ffmpeg and "anullsrc=r=44100:cl=stereo" in fake_ffmpeg[0]


@pytest.mark.asyncio
async def test_station_is_created_even_when_generation_fails(client, auth_headers, db_session, monkeypatch):
    """V1-85: a failure while generating the blackouts is logged and the station is still made"""
    from app.services import shabbos_service

    def broken(*a, **k):
        raise RuntimeError("sun calculation broke")

    monkeypatch.setattr(shabbos_service, "generate_shabbos_windows", broken)
    messages = []

    class Keep(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    log = logging.getLogger("app.services.station_service")
    handler = Keep(level=logging.WARNING)
    log.addHandler(handler)
    try:
        r = await client.post("/api/v1/stations", json={"name": "Still Made", "latitude": LAKEWOOD[0],
                                                        "longitude": LAKEWOOD[1]}, headers=auth_headers)
    finally:
        log.removeHandler(handler)
    assert r.status_code == 201, r.text
    assert await db_session.get(Station, uuid.UUID(r.json()["id"])) is not None
    assert any("Holiday auto-generation failed" in m and "sun calculation broke" in m for m in messages)
    assert (await db_session.execute(select(func.count(HolidayWindow.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_time_zone_sent_with_the_location_is_kept(client, auth_headers, db_session, fake_ffmpeg):
    """V1-86: the location sets the time zone only when the request does not name one.
    FIXED: a time zone chosen in the same request was overwritten by the one derived
    from the coordinates. "UTC" (the default) still counts as not chosen."""
    lat, lon, _ = JERUSALEM
    chosen = await client.post("/api/v1/stations", json={"name": "Chosen", "latitude": lat, "longitude": lon,
                                                         "timezone": "Europe/London"}, headers=auth_headers)
    assert chosen.status_code == 201 and chosen.json()["timezone"] == "Europe/London"
    derived = await client.post("/api/v1/stations", json={"name": "Derived", "latitude": lat, "longitude": lon},
                                headers=auth_headers)
    assert derived.json()["timezone"] == "Asia/Jerusalem"
    placeholder = await client.post("/api/v1/stations", json={"name": "Placeholder", "latitude": lat,
                                                              "longitude": lon, "timezone": "UTC"},
                                    headers=auth_headers)
    assert placeholder.json()["timezone"] == "Asia/Jerusalem"

    sid = derived.json()["id"]
    both = await client.put(f"/api/v1/stations/{sid}", json={"latitude": LAKEWOOD[0], "longitude": LAKEWOOD[1],
                                                             "timezone": "America/Chicago"}, headers=auth_headers)
    assert both.status_code == 200 and both.json()["timezone"] == "America/Chicago"
    moved = await client.put(f"/api/v1/stations/{sid}", json={"latitude": LOS_ANGELES[0],
                                                              "longitude": LOS_ANGELES[1]}, headers=auth_headers)
    assert moved.json()["timezone"] == "America/Los_Angeles"


# ── V1-88: holidays endpoints refuse ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["preview", "auto-generate"])
async def test_holidays_refuse_unknown_station_and_station_without_location(client, auth_headers, db_session, path):
    """V1-88: unknown station -> 404; a station without latitude/longitude -> 400 saying so"""
    r = await client.post(f"/api/v1/holidays/{path}", json={"station_id": str(uuid.uuid4())}, headers=auth_headers)
    assert r.status_code == 404 and r.json()["detail"] == "Station not found"
    st = Station(id=uuid.uuid4(), name="Nowhere", timezone="America/New_York")
    db_session.add(st)
    await db_session.commit()
    r = await client.post(f"/api/v1/holidays/{path}", json={"station_id": str(st.id)}, headers=auth_headers)
    assert r.status_code == 400
    assert "latitude and longitude" in r.json()["detail"]
    assert (await db_session.execute(select(func.count(HolidayWindow.id)))).scalar() == 0


# ── V1-89: manual blackouts ───────────────────────────────────────────────


async def _playing_station(db, name):
    st = Station(id=uuid.uuid4(), name=name, timezone="America/New_York")
    db.add(st)
    songs = [Asset(id=uuid.uuid4(), title=f"{name} {i}", duration=200.0, asset_type="music",
                   file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3") for i in range(3)]
    db.add_all(songs)
    await db.flush()
    db.add_all([QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=a.id, position=i + 1,
                           status="playing" if i == 0 else "pending", source="auto",
                           started_at=datetime.now(UTC) - timedelta(seconds=30) if i == 0 else None)
                for i, a in enumerate(songs)])
    await db.commit()
    return st, songs


async def _on_air(db, station_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalars().all()


@pytest.mark.asyncio
async def test_blackout_for_some_stations_silences_only_those(client, auth_headers, db_session):
    """V1-89: a manual blackout listing one station silences that station only"""
    from app.services.scheduler_engine import SchedulerEngine

    silence = await _silence(db_session)
    quiet, _ = await _playing_station(db_session, "Quiet")
    loud, loud_songs = await _playing_station(db_session, "Loud")
    now = datetime.now(UTC)
    r = await client.post("/api/v1/holidays", json={
        "name": "Memorial", "start_datetime": (now - timedelta(minutes=5)).isoformat(),
        "end_datetime": (now + timedelta(hours=2)).isoformat(), "is_blackout": True,
        "affected_stations": {"station_ids": [str(quiet.id)]},
    }, headers=auth_headers)
    assert r.status_code in (200, 201), r.text

    engine = SchedulerEngine()
    assert await engine._is_station_blacked_out(db_session, quiet, now)
    assert not await engine._is_station_blacked_out(db_session, loud, now)
    assert await queue_module._is_blacked_out(db_session, quiet.id)
    assert not await queue_module._is_blacked_out(db_session, loud.id)

    await queue_module.fill_blackout_queue(db_session, quiet.id)
    await queue_module.fill_blackout_queue(db_session, loud.id)
    silence_rows = (await db_session.execute(
        select(QueueEntry.station_id).where(QueueEntry.asset_id == silence.id)
    )).scalars().all()
    assert silence_rows and {str(s) for s in silence_rows} == {str(quiet.id)}
    assert [e.asset_id for e in await _on_air(db_session, loud.id)] == [loud_songs[0].id]


@pytest.mark.asyncio
async def test_ending_a_blackout_early_ends_the_silence_at_once(client, auth_headers, db_session):
    """V1-89 (B-JOB-40): moving the end of a blackout that is on to now ends the silence
    on the next advance, not when the silence item runs out; music comes back"""
    silence = await _silence(db_session)
    st, songs = await _playing_station(db_session, "Early End")
    now = datetime.now(UTC)
    r = await client.post("/api/v1/holidays", json={
        "name": "Short notice", "start_datetime": (now - timedelta(minutes=10)).isoformat(),
        "end_datetime": (now + timedelta(hours=3)).isoformat(), "is_blackout": True,
        "affected_stations": {"station_ids": [str(st.id)]},
    }, headers=auth_headers)
    hid = r.json()["id"]
    await queue_module.fill_blackout_queue(db_session, st.id)
    # Song on air ends: silence goes on
    for e in await _on_air(db_session, st.id):
        e.started_at = now - timedelta(seconds=300)
    await db_session.commit()
    started = await queue_module._check_advance(db_session, st.id)
    assert started is not None and started.asset_id == silence.id

    r = await client.put(f"/api/v1/holidays/{hid}",
                         json={"end_datetime": (datetime.now(UTC) - timedelta(seconds=5)).isoformat()},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    assert not await queue_module._is_blacked_out(db_session, st.id)
    after = await queue_module._check_advance(db_session, st.id)
    assert after is not None and after.asset_id in {a.id for a in songs}
    left = (await db_session.execute(
        select(func.count(QueueEntry.id)).where(QueueEntry.asset_id == silence.id,
                                                QueueEntry.status.in_(["pending", "playing"]))
    )).scalar()
    assert left == 0


# ── V1-91: deleting an item a spot schedule or program block uses ─────────


@pytest.mark.asyncio
async def test_deleting_an_item_in_a_spot_schedule_is_refused(client, auth_headers, db_session):
    """V1-91: FIXED: the spot schedule used to vanish with the item (database cascade),
    without a word; now 409 names the schedule and both stay"""
    spot = Asset(id=uuid.uuid4(), title="Ruvy Tire", duration=30.0, asset_type="spot", file_path="s.mp3")
    sponsor = Sponsor(id=uuid.uuid4(), name="Ruvy Tire", length_seconds=30, audio_file_path="s.mp3")
    db_session.add_all([spot, sponsor])
    await db_session.flush()
    sched = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, name="Ruvy weekdays")
    db_session.add(sched)
    await db_session.commit()

    r = await client.delete(f"/api/v1/assets/{spot.id}", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "spot schedule 'Ruvy weekdays'" in r.json()["detail"]
    assert await db_session.get(Asset, spot.id) is not None
    assert (await db_session.execute(select(SpotSchedule).where(SpotSchedule.id == sched.id))).scalar_one()


@pytest.mark.asyncio
async def test_deleting_an_item_in_a_program_block_is_refused(client, auth_headers, db_session):
    """V1-91: FIXED: a program block item named by id was left pointing at nothing; now
    409 names the block. An item nothing uses still deletes."""
    station = Station(id=uuid.uuid4(), name="Block FM", timezone="America/New_York")
    jingle = Asset(id=uuid.uuid4(), title="Station ID", duration=8.0, asset_type="jingle", file_path="j.mp3")
    spare = Asset(id=uuid.uuid4(), title="Spare", duration=8.0, asset_type="jingle", file_path="x.mp3")
    db_session.add_all([station, jingle, spare])
    await db_session.flush()
    db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Weather Sponsors", times="12:00",
                                items=[{"kind": "time"}, {"kind": "asset", "asset_id": str(jingle.id)}]))
    await db_session.commit()

    r = await client.delete(f"/api/v1/assets/{jingle.id}", headers=auth_headers)
    assert r.status_code == 409 and "program block 'Weather Sponsors'" in r.json()["detail"]
    assert await db_session.get(Asset, jingle.id) is not None
    r = await client.delete(f"/api/v1/assets/{spare.id}", headers=auth_headers)
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_block_skips_an_item_that_is_gone(db_session):
    """V1-91: a block whose recording is missing anyway (removed before this check)
    still airs its other items and reports the missing one"""
    from app.services.program_block_service import plan_blocks, reconcile_block_entries

    station = Station(id=uuid.uuid4(), name="Gap FM", timezone="America/New_York")
    jingle = Asset(id=uuid.uuid4(), title="Station ID", duration=8.0, asset_type="jingle", file_path="j.mp3")
    db_session.add_all([station, jingle])
    await db_session.flush()
    db_session.add(ProgramBlock(
        id=uuid.uuid4(), station_id=station.id, name="Top of hour",
        times=",".join(f"{h:02d}:00" for h in range(24)),
        items=[{"kind": "asset", "asset_id": str(uuid.uuid4()), "label": "Old promo"},
               {"kind": "asset", "asset_id": str(jingle.id)}]))
    await db_session.commit()

    plan = await plan_blocks(db_session, station, hours=3)
    assert plan and plan[0].problems == ["Old promo: the recording was removed from the library"]
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    queued = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.source == "block")
    )).scalars().all()
    assert queued and {e.asset_id for e in queued} == {jingle.id}
