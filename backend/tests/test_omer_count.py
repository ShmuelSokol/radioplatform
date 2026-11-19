"""Sefiras HaOmer on the hour (services/omer_count.py, wiki/omer-count.md).

Shmuel, 2026-11-19: "we need to announce the Omer on the hour and it switches to the
next Omer day after Shekia"; counted as the siddur, days only: "Hayom yom echad ba'omer
... We're not counting days and nights, it's days only".
"""
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
import time_machine
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.asset import Asset, fit_for_random_pick
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import omer_count as omer
from app.services.queue_replenish_service import QueueReplenishService
from tests.test_weather_spots import _patches

NY = ZoneInfo("America/New_York")
JLM = ZoneInfo("Asia/Jerusalem")


# ── the calendar ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("year,first,last", [
    (5787, date(2027, 4, 23), date(2027, 6, 10)),
    (5788, date(2028, 4, 12), date(2028, 5, 30)),
    (5786, date(2026, 4, 3), date(2026, 5, 21)),
])
def test_the_49_days(year, first, last):
    assert omer.omer_start(year) == first
    assert omer.omer_day_on(first) == 1 and omer.omer_day_on(last) == 49
    assert omer.omer_day_on(first - timedelta(days=1)) is None
    assert omer.omer_day_on(last + timedelta(days=1)) is None
    assert omer.omer_day_on(first + timedelta(days=32)) == 33  # Lag BaOmer, 18 Iyar


def test_next_count():
    assert omer.next_count(date(2026, 11, 19)) == (date(2027, 4, 23), date(2027, 6, 10))
    assert omer.next_count(date(2027, 5, 1)) == (date(2027, 4, 23), date(2027, 6, 10))
    assert omer.next_count(date(2027, 6, 11))[0] == date(2028, 4, 12)


@pytest.mark.parametrize("zone,place", [(NY, omer.LAKEWOOD), (JLM, omer.JERUSALEM)])
def test_switches_at_shekia_in_each_city(zone, place):
    day = date(2027, 5, 3)  # day 11
    assert omer.omer_day_on(day) == 11
    sunrise, sunset = omer._sun(place, zone, day)
    assert omer.omer_at(sunset - timedelta(seconds=30), zone, place) == 11
    assert omer.omer_at(sunset + timedelta(seconds=30), zone, place) == 12
    assert omer.omer_at(sunrise - timedelta(minutes=1), zone, place) == 11
    assert omer.omer_at(sunrise + timedelta(minutes=1), zone, place) == 11
    # a later switch (minutes after shekia) keeps the day's count until then
    assert omer.omer_at(sunset + timedelta(minutes=30), zone, place, 42) == 11
    assert omer.omer_at(sunset + timedelta(minutes=43), zone, place, 42) == 12


def test_cities_differ_by_their_own_shekia():
    """At one moment Jerusalem has passed shekia while Lakewood has not."""
    at = datetime(2027, 5, 3, 17, 0, tzinfo=UTC)  # 8 PM Jerusalem, 1 PM Lakewood
    assert omer.omer_at(at, JLM, omer.JERUSALEM) == 12
    assert omer.omer_at(at, NY, omer.LAKEWOOD) == 11


def test_first_and_last():
    first_night = datetime(2027, 4, 22, 21, 0, tzinfo=NY)  # second Seder night
    assert omer.omer_at(first_night, NY, omer.LAKEWOOD) == 1
    assert omer.omer_at(datetime(2027, 4, 22, 12, 0, tzinfo=NY), NY, omer.LAKEWOOD) is None
    erev_shavuos = datetime(2027, 6, 10, 12, 0, tzinfo=NY)
    assert omer.omer_at(erev_shavuos, NY, omer.LAKEWOOD) == 49
    assert omer.omer_at(datetime(2027, 6, 10, 21, 0, tzinfo=NY), NY, omer.LAKEWOOD) is None


def test_wording_counts_days_only_as_the_siddur():
    """Shmuel 2026-11-19: "Hayom yom echad ba'omer ... We're not counting days and
    nights, it's days only": "Today is ..." at night too."""
    assert omer.wording(1) == "Today is the first day of the Omer."
    assert omer.wording(6) == "Today is the sixth day of the Omer."
    assert omer.wording(7) == "Today is the seventh day, which is one week of the Omer."
    assert omer.wording(8) == "Today is the eighth day, which is one week and one day of the Omer."
    assert omer.wording(9) == "Today is the ninth day, which is one week and two days of the Omer."
    assert omer.wording(14) == "Today is the fourteenth day, which is two weeks of the Omer."
    assert omer.wording(23) == "Today is the twenty-third day, which is three weeks and two days of the Omer."
    # no Lag BaOmer line (Shmuel: "Don't say it's Lag BaOmer in the counting")
    assert omer.wording(33) == "Today is the thirty-third day, which is four weeks and five days of the Omer."
    assert omer.wording(42) == "Today is the forty-second day, which is six weeks of the Omer."
    assert omer.wording(49) == "Today is the forty-ninth day, which is seven weeks of the Omer."
    scripts = omer.all_scripts()
    assert len(scripts) == 49 and [s["file"] for s in scripts[:2]] == ["omer-01.mp3", "omer-02.mp3"]
    assert not any("night" in s["text"].lower() for s in scripts)
    for s in scripts:  # weeks and days always add up to the day
        n = s["day"]
        if n >= 7:
            weeks, days = divmod(n, 7)
            assert omer.weeks_and_days(n).startswith(f"{omer.cardinal(weeks)} week")
            assert ("and" in omer.weeks_and_days(n)) == bool(days)
    assert not any("Lag" in s["text"] for s in scripts)
    with pytest.raises(ValueError):
        omer.wording(50)


def test_setting():
    assert omer.omer_setting(None) == omer.OmerSetting(True, 0)
    assert omer.omer_setting({"omer_count": {"enabled": False}}).enabled is False
    assert omer.omer_setting({"omer_count": {"switch_minutes": 500}}).switch_minutes == omer.MAX_SWITCH_MINUTES
    assert omer.omer_setting({"omer_count": {"switch_minutes": "x"}}).switch_minutes == 0


def test_place():
    assert omer.station_place(None, None, "Asia/Jerusalem") == omer.JERUSALEM
    assert omer.station_place(None, None, "America/New_York") == omer.LAKEWOOD
    assert omer.station_place(None, None, "Europe/London") is None
    assert omer.station_place(51.5, -0.1, "Europe/London") == (51.5, -0.1)


# ── the queue ─────────────────────────────────────────────────────────────────

def _clips(db) -> dict:
    out = {}
    for n in range(1, 50):
        a = Asset(id=uuid.uuid4(), title=f"Omer - day {n}", duration=6.0, file_path=f"o{n}.mp3",
                  asset_type="jingle", category="omer_count", metadata_extra={"omer_day": f"{n:02d}"})
        out[n] = a
        db.add(a)
    return out


async def _station(db, tz="America/New_York", config=None):
    station = Station(id=uuid.uuid4(), name=f"Main {uuid.uuid4().hex[:4]}", timezone=tz, is_active=True,
                      automation_config={"hourly_time_announcement": True, "weather_enabled": True,
                                         "blocks_keep_hourly": True, **(config or {})})
    db.add(station)
    await db.flush()
    return station


async def _fill(db: AsyncSession, station, clock=None, weather=None, hourly=True):
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        svc = QueueReplenishService(db, station.id)
        svc.max_pos = 100 + len((await db.execute(select(QueueEntry))).scalars().all()) * 10
        await svc._load_automation_config()
        if hourly:
            await svc._schedule_hourly_announcements()
        await svc._schedule_omer_counts()
        await db.commit()


async def _groups(db, station) -> dict:
    rows = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )).scalars().all()
    out: dict = {}
    for e in rows:
        at = e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC)
        out.setdefault(at, []).append(e)
    return out


def _said(groups, clips):
    by_id = {a.id: k for k, a in clips.items()}
    return {at: by_id[e.asset_id] for at, es in groups.items() for e in es if e.asset_id in by_id}


@pytest.mark.asyncio
async def test_each_hour_says_its_count_after_the_time_check(db_session, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    clock = Asset(id=uuid.uuid4(), title="Time", duration=5.0, file_path="t.mp3", asset_type="jingle",
                  category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w.mp3", asset_type="spot",
                    category="weather_spot")
    db_session.add_all([clock, weather])
    clips = _clips(db_session)
    station = await _station(db_session)
    with time_machine.travel(datetime(2027, 5, 3, 9, 10, tzinfo=NY), tick=False):  # Monday, day 11
        await _fill(db_session, station, clock, weather)
        groups = await _groups(db_session, station)
        said = _said(groups, clips)
        assert len(said) == 48
        for at, entries in groups.items():
            ids = [e.asset_id for e in entries]
            if at.astimezone(NY).minute == 30:
                assert ids == [weather.id], at  # the count is on the hour only
                continue
            assert ids == [clock.id, clips[said[at]].id, weather.id], at
            assert {e.timing_mode for e in entries} == {"hard"}
            assert said[at] == omer.omer_at(at, NY, omer.LAKEWOOD)
        local = {at.astimezone(NY).strftime("%a %H"): v for at, v in said.items()}
        assert local["Mon 12"] == 11 and local["Mon 20"] == 12
        assert local["Tue 05"] == 12 and local["Tue 07"] == 12
        # a second pass changes nothing
        before = {(e.id, e.position) for es in groups.values() for e in es}
        await _fill(db_session, station, clock, weather)
        assert {(e.id, e.position) for es in (await _groups(db_session, station)).values() for e in es} == before


@pytest.mark.asyncio
async def test_time_check_added_later_keeps_the_count_behind_it(db_session, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    clock = Asset(id=uuid.uuid4(), title="Time", duration=5.0, file_path="t.mp3", asset_type="jingle",
                  category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w.mp3", asset_type="spot",
                    category="weather_spot")
    db_session.add_all([clock, weather])
    clips = _clips(db_session)
    station = await _station(db_session)
    with time_machine.travel(datetime(2027, 5, 3, 9, 10, tzinfo=NY), tick=False):
        await _fill(db_session, station, hourly=False)  # the count first, nothing else
        await _fill(db_session, station, clock, weather)
        for at, entries in (await _groups(db_session, station)).items():
            if at.astimezone(NY).minute == 30:
                continue
            assert [e.asset_id for e in entries][0::2] == [clock.id, weather.id], at
            assert entries[1].asset_id in {a.id for a in clips.values()}


@pytest.mark.asyncio
async def test_nothing_in_shabbos_outside_the_omer_or_without_a_clip(db_session):
    clips = _clips(db_session)
    del clips[12]
    await db_session.flush()
    missing = (await db_session.execute(select(Asset).where(Asset.title == "Omer - day 12"))).scalar_one()
    await db_session.delete(missing)
    station = await _station(db_session)
    # Shabbos May 7-8, 2027 in Lakewood (as the window generator would make it)
    start = datetime(2027, 5, 7, 19, 39, tzinfo=NY)
    end = datetime(2027, 5, 8, 21, 11, tzinfo=NY)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start.astimezone(UTC),
                                 end_datetime=end.astimezone(UTC), is_blackout=True,
                                 affected_stations={"station_ids": [str(station.id)]}, reason="Shabbos"))
    await db_session.flush()
    with time_machine.travel(datetime(2027, 5, 7, 6, 10, tzinfo=NY), tick=False):
        await _fill(db_session, station, hourly=False)
        said = _said(await _groups(db_session, station), clips)
    hours = sorted(said)
    assert hours, "nothing queued"
    assert not any(start <= at < end for at in hours)
    assert max(at for at in hours if at < start).astimezone(NY).hour == 19  # 7 PM Friday, before candles
    assert min(at for at in hours if at > end).astimezone(NY).hour == 22  # 10 PM Motzei Shabbos
    assert said[min(at for at in hours if at > end)] == 17  # Shabbos was day 16

    # day 12 (from Monday evening May 3) has no recording: nothing for it, not a stand-in
    with time_machine.travel(datetime(2027, 5, 3, 9, 10, tzinfo=NY), tick=False):
        await _fill(db_session, station, hourly=False)
        said = _said(await _groups(db_session, station), clips)
        assert 12 not in said.values()

    # far from the Omer nothing at all, and no database work for it
    other = await _station(db_session)
    with time_machine.travel(datetime(2027, 1, 10, 9, 10, tzinfo=NY), tick=False):
        await _fill(db_session, other, hourly=False)
        assert await _groups(db_session, other) == {}


@pytest.mark.asyncio
async def test_switch_off_and_later_switch_rework_the_queue(db_session):
    clips = _clips(db_session)
    station = await _station(db_session)
    with time_machine.travel(datetime(2027, 5, 3, 9, 10, tzinfo=NY), tick=False):
        await _fill(db_session, station, hourly=False)
        assert len(_said(await _groups(db_session, station), clips)) == 48

        # 9 PM Monday is after shekia (7:53 PM); with the switch 72 minutes later it is still the day
        station.automation_config = {**station.automation_config, "omer_count": {"switch_minutes": 72}}
        await db_session.commit()
        await _fill(db_session, station, hourly=False)
        local = {at.astimezone(NY).strftime("%a %H"): v
                 for at, v in _said(await _groups(db_session, station), clips).items()}
        assert local["Mon 21"] == 11 and local["Mon 22"] == 12  # shekia 7:53 + 72
        assert len(local) == 48

        station.automation_config = {**station.automation_config, "omer_count": {"enabled": False}}
        await db_session.commit()
        await _fill(db_session, station, hourly=False)
        assert _said(await _groups(db_session, station), clips) == {}


@pytest.mark.asyncio
async def test_jerusalem_counts_by_jerusalem(db_session):
    clips = _clips(db_session)
    station = await _station(db_session, tz="Asia/Jerusalem")
    with time_machine.travel(datetime(2027, 5, 3, 9, 10, tzinfo=JLM), tick=False):
        await _fill(db_session, station, hourly=False)
        said = _said(await _groups(db_session, station), clips)
    local = {at.astimezone(JLM).strftime("%a %H"): v for at, v in said.items()}
    assert local["Mon 19"] == 11 and local["Mon 20"] == 12  # shekia 7:20 PM
    assert all(at.astimezone(JLM).minute == 0 for at in said)


def test_clips_are_never_random_filler():
    from sqlalchemy.dialects import sqlite

    sql = str(fit_for_random_pick().compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "omer_count" in sql


# ── the API ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_list_and_station_hours(client: AsyncClient, auth_headers: dict, db_session):
    p1, p2, p3 = _patches(6.2)
    with p1, p2, p3:
        r = await client.put("/api/v1/omer-count/8", files={"file": ("o.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
                             headers=auth_headers)
        again = await client.put("/api/v1/omer-count/8",
                                 files={"file": ("o.mp3", io.BytesIO(b"other audio"), "audio/mpeg")},
                                 headers=auth_headers)
        bad_day = await client.put("/api/v1/omer-count/50",
                                   files={"file": ("o.mp3", io.BytesIO(b"x"), "audio/mpeg")}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["recorded"] and r.json()["duration"] == 6.2
    assert r.json()["recorded_text"] == omer.wording(8)
    assert again.json()["asset_id"] == r.json()["asset_id"]
    assert bad_day.status_code == 422

    overview = (await client.get("/api/v1/omer-count", headers=auth_headers)).json()
    assert overview["total"] == 49 and overview["recorded"] == 1
    assert overview["first_day"] == "2027-04-23" and overview["last_day"] == "2027-06-10"
    assert [c["file"] for c in overview["clips"][:2]] == ["omer-01.mp3", "omer-02.mp3"]

    station = await _station(db_session)
    await db_session.commit()
    # Friday April 30, 2027 is day 8; from its shekia, day 9
    hours = (await client.get(f"/api/v1/omer-count/station/{station.id}", params={"on": "2027-04-30"},
                              headers=auth_headers)).json()
    assert hours["enabled"] and hours["place_known"] and len(hours["hours"]) == 24
    by = {h["local"]: h for h in hours["hours"]}
    assert by["12:00 PM"]["omer_day"] == 8 and by["1:00 AM"]["omer_day"] == 8
    assert by["9:00 PM"]["omer_day"] == 9 and by["9:00 PM"]["text"] == omer.wording(9)
    assert by["1:00 AM"]["text"] == omer.wording(8) and by["1:00 AM"]["recorded"]
    assert not by["9:00 PM"]["recorded"]
    assert hours["shekia"].endswith("PM")


# ── the whole Omer, planned day by day (seconds, no simulator) ────────────────

PLACES = {"America/New_York": omer.LAKEWOOD, "Asia/Jerusalem": omer.JERUSALEM}


async def _whole_omer(db, tz: str):
    """Run the Omer pass once a day (00:10 local) from Erev Pesach to after Shavuos with
    the windows the site generates, and return the station, its windows and every
    count queued: {UTC mark: day}."""
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    clips = _clips(db)
    station = await _station(db, tz=tz)
    lat, lon = PLACES[tz]
    first, last = date(2027, 4, 19), date(2027, 6, 14)
    windows = []
    for w in merge_overlapping_windows(
        generate_shabbos_windows(lat, lon, tz, first, last, [str(station.id)])
        + generate_yom_tov_windows(lat, lon, tz, first, last, [str(station.id)])
    ):
        s, e = datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])
        windows.append((s.astimezone(UTC), e.astimezone(UTC), w["reason"]))
        db.add(HolidayWindow(id=uuid.uuid4(), name=w["name"], start_datetime=s.astimezone(UTC),
                             end_datetime=e.astimezone(UTC), is_blackout=True,
                             affected_stations=w["affected_stations"], reason=w["reason"]))
    await db.flush()
    zone = ZoneInfo(tz)
    day = date(2027, 4, 20)
    while day <= date(2027, 6, 12):
        with time_machine.travel(datetime(day.year, day.month, day.day, 0, 10, tzinfo=zone), tick=False):
            await _fill(db, station, hourly=False)
        day += timedelta(days=1)
    return station, windows, _said(await _groups(db, station), clips)


@pytest.mark.asyncio
@pytest.mark.parametrize("tz", ["America/New_York", "Asia/Jerusalem"])
async def test_whole_omer_every_hour(db_session, tz):
    from app.services.queue_replenish_service import local_hour_marks

    station, windows, said = await _whole_omer(db_session, tz)
    zone, place = ZoneInfo(tz), PLACES[tz]

    def off_air(at):
        return any(s <= at < e for s, e, _ in windows)

    start = datetime(2027, 4, 20, 1, 0, tzinfo=zone)
    every = local_hour_marks(start.astimezone(UTC), zone, 24 * 53)
    expected = {m: omer.omer_at(m, zone, place) for m in every
                if not off_air(m) and omer.omer_at(m, zone, place) is not None}
    assert said == expected
    # every one of the 49 days is said in some hour unless the whole day is silent
    assert set(said.values()) == set(expected.values())
    local = {m.astimezone(zone): v for m, v in said.items()}
    first = min(local)
    lag = [t for t, v in local.items() if v == 33]
    last = max(local)
    if tz == "America/New_York":
        # 2027: two days Yom Tov (Thursday, Friday) run into Shabbos, so the first count
        # airs Motzei Shabbos, the third night
        assert local[first] == 3 and first.date() == date(2027, 4, 24)
    else:
        # one day Yom Tov: the first night is on the air after it ends
        assert local[first] == 1 and first.date() == date(2027, 4, 22)
    # Lag BaOmer (Tuesday May 25, 2027): its night Monday evening and its day
    assert {t.date() for t in lag} == {date(2027, 5, 24), date(2027, 5, 25)}
    # the last is Erev Shavuos, day 49, before candle lighting
    assert local[last] == 49 and last.date() == date(2027, 6, 10)
    shavuos = next(s for s, _, reason in windows if "Shavu" in reason)
    assert last.astimezone(UTC) < shavuos
    # nothing in any Shabbos or Yom Tov, only on the hour
    assert not any(off_air(m) for m in said)
    assert {m.astimezone(zone).minute for m in said} == {0}
    # each Motzei Shabbos the next night's count is back within the hour after the window
    for s, e, reason in windows:
        if reason == "Shabbos" and date(2027, 4, 24) <= e.astimezone(zone).date() <= date(2027, 6, 5):
            back = min(m for m in said if m >= e)
            assert back - e < timedelta(hours=1)
            assert said[back] == omer.omer_at(back, zone, place)
