"""A whole year of the plan, checked without playing it (Shmuel 2026-11-18: "any way to
simulate without needing to simulate it?").

The code that decides when Shabbos and Yom Tov silence falls, when the hourly time check
is due and when program blocks air is asked for a year directly. It takes seconds and
covers every Friday, every Yom Tov and both clock changes, for Lakewood and Jerusalem.
The simulator (tests/sim) then only has to show that the queue follows the plan.
"""
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pyluach.dates import HebrewDate

from app.services.program_block_service import occurrences_between
from app.services.queue_replenish_service import local_hour_marks
from app.services.shabbos_service import (
    generate_shabbos_windows,
    generate_yom_tov_windows,
    merge_overlapping_windows,
)
from app.services.sun_service import get_sun_times

PLACES = {
    "lakewood": (40.0821, -74.2097, "America/New_York", 18),
    "jerusalem": (31.7683, 35.2137, "Asia/Jerusalem", 40),
}
START, END = date(2026, 11, 19), date(2027, 11, 19)
FIXTURE = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"


def _windows(place):
    lat, lon, tz, _ = PLACES[place]
    shabbos = generate_shabbos_windows(lat, lon, tz, START, END, ["s"])
    yom_tov = generate_yom_tov_windows(lat, lon, tz, START, END, ["s"])
    merged = merge_overlapping_windows(shabbos + yom_tov)
    return shabbos, yom_tov, [
        (datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"]), w)
        for w in merged
    ]


@pytest.mark.parametrize("place", list(PLACES))
def test_every_shabbos_of_the_year_is_candle_lighting_to_72_minutes_after_shekia(place):
    lat, lon, tz, candles = PLACES[place]
    zone = ZoneInfo(tz)
    shabbos, _, merged = _windows(place)
    assert 51 <= len(shabbos) <= 53
    day = START
    fridays = 0
    while day <= END - timedelta(days=1):
        if day.weekday() == 4:
            fridays += 1
            lighting = get_sun_times(lat, lon, tz, day)["sunset"] - timedelta(minutes=candles)
            out = get_sun_times(lat, lon, tz, day + timedelta(days=1))["sunset"] + timedelta(minutes=72)
            cover = [(s, e) for s, e, _ in merged if s <= lighting + timedelta(seconds=1) and e >= out - timedelta(seconds=1)]
            assert cover, f"{place}: Shabbos of {day} is not covered from candle lighting to shekia + 72"
            s, e = cover[0]
            # a plain Shabbos is exactly that; next to Yom Tov the window is longer, never shorter
            if (e - s) < timedelta(hours=30):
                assert s == lighting and e == out, (place, day)
            # on the station's own Friday afternoon and Saturday evening, whatever the clock change
            assert lighting.astimezone(zone).weekday() == 4 and 15 <= lighting.astimezone(zone).hour <= 20
            assert out.astimezone(zone).weekday() == 5 and 17 <= out.astimezone(zone).hour <= 22
        day += timedelta(days=1)
    assert fridays >= 52


@pytest.mark.parametrize("place", list(PLACES))
def test_every_yom_tov_of_the_year_is_silent_for_all_its_days(place):
    lat, lon, tz, candles = PLACES[place]
    israel = place == "jerusalem"
    _, yom_tov, merged = _windows(place)
    # 5787's Pesach and Shavuos and 5788's Tishrei fall in the year
    days = {
        "Pesach first": [HebrewDate(5787, 1, 15)] + ([] if israel else [HebrewDate(5787, 1, 16)]),
        "Pesach last": [HebrewDate(5787, 1, 21)] + ([] if israel else [HebrewDate(5787, 1, 22)]),
        "Shavuos": [HebrewDate(5787, 3, 6)] + ([] if israel else [HebrewDate(5787, 3, 7)]),
        "Rosh Hashanah": [HebrewDate(5788, 7, 1), HebrewDate(5788, 7, 2)],  # two days in Israel too
        "Yom Kippur": [HebrewDate(5788, 7, 10)],
        "Sukkos": [HebrewDate(5788, 7, 15)] + ([] if israel else [HebrewDate(5788, 7, 16)]),
        "Shemini Atzeres": [HebrewDate(5788, 7, 22)] + ([] if israel else [HebrewDate(5788, 7, 23)]),
    }
    for name, hebrew in days.items():
        first, last = hebrew[0].to_pydate(), hebrew[-1].to_pydate()
        lighting = get_sun_times(lat, lon, tz, first - timedelta(days=1))["sunset"] - timedelta(minutes=candles)
        out = get_sun_times(lat, lon, tz, last)["sunset"] + timedelta(minutes=72)
        cover = [(s, e) for s, e, _ in merged if s <= lighting and e >= out]
        assert len(cover) == 1, f"{place}: {name} ({first} to {last}) is not one silent window"
    if israel:
        # the second day of galuyos is an ordinary day in Jerusalem: on the air at noon
        # (unless it is Shabbos)
        for h in (HebrewDate(5787, 1, 16), HebrewDate(5787, 3, 7), HebrewDate(5788, 7, 16), HebrewDate(5788, 7, 23)):
            d = h.to_pydate()
            if d.weekday() == 5:
                continue
            noon = datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(tz))
            assert not any(s <= noon <= e for s, e, _ in merged), f"Jerusalem silent on {d}"


@pytest.mark.parametrize("place", list(PLACES))
def test_yom_tov_next_to_shabbos_is_one_window_with_no_music_between(place):
    _, _, merged = _windows(place)
    merged.sort(key=lambda x: x[0])
    for (s1, e1, _), (s2, e2, _) in zip(merged, merged[1:]):
        assert s2 > e1, "merged windows must not overlap"
        # two silences never sit a few hours apart with a sliver of programming between
        # a day that is still Yom Tov or Shabbos
        assert (s2 - e1) > timedelta(hours=20), (place, e1, s2)


@pytest.mark.parametrize("tz", ["America/New_York", "Asia/Jerusalem", "Asia/Kolkata", "Asia/Kathmandu"])
def test_a_year_of_hourly_time_checks_one_per_local_hour(tz):
    """V1-115 / V1-116 / V3-11: every local hh:00 of the year gets exactly one time check,
    on both clock-change days (1 AM twice in autumn, no 2 AM in spring: US and Israel
    change on different dates) and in half-hour and 45-minute zones."""
    zone = ZoneInfo(tz)
    now = datetime(2026, 11, 19, 0, 0, 30, tzinfo=UTC)
    marks = local_hour_marks(now, zone, 24 * 365)
    assert len(marks) == len(set(marks))
    gaps = {(b - a) for a, b in zip(marks, marks[1:])}
    assert gaps <= {timedelta(minutes=30), timedelta(hours=1), timedelta(minutes=90)}, gaps
    for at in marks:
        local = at.astimezone(zone)
        assert local.minute == 0 and local.second == 0
    # one mark for every hour that elapses, except where a zone's clock change is not a whole hour
    assert abs(len(marks) - 24 * 365) <= 2
    if tz in ("America/New_York", "Asia/Jerusalem"):
        assert gaps == {timedelta(hours=1)}  # a real hour apart even when the label repeats or skips
        labels = [(m.astimezone(zone).date(), m.astimezone(zone).hour) for m in marks]
        twice = {x for x in labels if labels.count(x) > 1} if len(set(labels)) != len(labels) else set()
        assert len(twice) == 1 and next(iter(twice))[1] == 1  # the autumn 1 AM, once in the year


def _blocks():
    data = json.loads(FIXTURE.read_text())
    stations = {s["id"]: s for s in data["stations"]}
    return [
        (SimpleNamespace(id=b["id"], name=b["name"], days_of_week=b["days_of_week"], times=b["times"],
                         season_start=b.get("season_start"), season_end=b.get("season_end")),
         ZoneInfo(stations[b["station_id"]]["timezone"]))
        for b in data["program_blocks"] if b["is_active"]
    ]


def test_a_year_of_the_stations_real_program_blocks():
    """V3-12 and the clock-change part of V1-115: every real block airs at each of its
    clock times once per local day, on its own weekdays, all year; a time that does not
    exist (spring 2 AM) is skipped and none airs twice."""
    start = datetime(2026, 11, 19, 5, 0, tzinfo=UTC)
    end = start + timedelta(days=365)
    for block, zone in _blocks():
        found = occurrences_between([block], zone, start, end)
        assert len({o.at for o in found}) == len(found), block.name
        times = [t for t in block.times.split(",") if t]
        weekdays = {int(d) for d in block.days_of_week.split(",") if d != ""}
        per_day: dict = {}
        for o in found:
            assert o.local.weekday() in weekdays, (block.name, o.local)
            assert o.local.strftime("%H:%M") in times, (block.name, o.local)
            per_day.setdefault(o.local.date(), []).append(o.local.strftime("%H:%M"))
        for day, aired in per_day.items():
            if day in (start.astimezone(zone).date(), end.astimezone(zone).date()):
                continue
            missing = set(times) - set(aired)
            # only a clock time that does not exist that day may be missing
            for t in missing:
                h, m = map(int, t.split(":"))
                local = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
                assert local.astimezone(UTC).astimezone(zone).hour != h, (block.name, day, t)
            # the autumn hour that comes twice airs once (the first), never two of one label
            assert len(aired) == len(set(aired)), (block.name, day)
        active_days = sum(1 for n in range(1, 364) if (start + timedelta(days=n)).astimezone(zone).weekday() in weekdays)
        assert len(per_day) >= active_days - 2, block.name
