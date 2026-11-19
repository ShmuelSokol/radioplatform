"""Yom Tov in the station simulator (tests/sim/yomtov.py, wiki/station-simulator.md).

The whole of every Yom Tov of the coming year runs with
`uv run python -m tests.sim.yomtov --station main --index 0` (list them with --list);
here only the two edges of one: into the silence and back out of it.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.sim.run import simulate
from tests.sim.yomtov import stretches

EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
SILENCE = "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"
YEAR = (date(2026, 11, 19), date(2027, 11, 19))


def _named(station: str, word: str) -> dict:
    for group in stretches(station, *YEAR):
        for w in group:
            if word in w["name"]:
                return w
    raise AssertionError(f"no {word} window for {station}")


def test_every_yom_tov_of_the_year_is_listed():
    """Seven stretches per station; Jerusalem keeps one day except Rosh Hashanah, and a
    Yom Tov that runs into Shabbos is one window with no gap."""
    for station in ("main", "stories", "jerusalem"):
        groups = stretches(station, *YEAR)
        names = " | ".join(w["name"] for g in groups for w in g)
        for word in ("Pesach (First Days)", "Pesach (Last Days)", "Shavuot", "Rosh Hashanah", "Yom Kippur",
                     "Sukkot", "Shemini Atzeret"):
            assert word in names, (station, word)
        assert len(groups) == 7, (station, names)
    hours = lambda w: (w["end"] - w["start"]).total_seconds() / 3600  # noqa: E731
    # Pesach 5787 starts Wednesday night: abroad two days of Yom Tov run into Shabbos
    assert 72 < hours(_named("main", "Pesach (First Days)")) < 75
    assert 25 < hours(_named("jerusalem", "Pesach (First Days)")) < 27
    # Rosh Hashanah is two days in Jerusalem too (here Shabbos and Sunday)
    assert 49 < hours(_named("jerusalem", "Rosh Hashanah")) < 51
    assert 25 < hours(_named("jerusalem", "Yom Kippur")) < 27


@pytest.mark.asyncio
async def test_yom_kippur_starts_with_silence_at_candle_lighting(tmp_path):
    """Main, Erev Yom Kippur 5788 (Sunday October 10, 2027): the item on air fades at
    candle lighting and only silence follows."""
    w = _named("main", "Yom Kippur")
    start = (w["start"] - timedelta(minutes=12)).replace(second=0, microsecond=0)
    _, result = await simulate(EXPORT, start, 0.4, ["main"], tmp_path, regenerate_windows=True)
    print(result.report())
    assert result.checks_run[SILENCE] >= 1
    assert not result.failed(SILENCE), [f.text for f in result.failed(SILENCE)]
    assert not result.failed("no dead air or emergency music")


@pytest.mark.asyncio
async def test_station_is_back_seconds_after_yom_tov_ends(tmp_path):
    """Jerusalem, the end of the last day of Pesach 5787: the silence file on air is cut
    and music is back within seconds of 72 minutes after shekia, not when the 5-minute
    file runs out."""
    w = _named("jerusalem", "Pesach (Last Days)")
    start = (w["end"] - timedelta(minutes=11)).replace(second=0, microsecond=0)
    _, result = await simulate(EXPORT, start, 0.4, ["jerusalem"], tmp_path, regenerate_windows=True)
    print(result.report())
    end = w["end"].timestamp()
    back = next(r for r in result.rows if r.kind != "silence" and r.lane != "emergency" and r.start > end - 1)
    assert 0 <= back.start - end <= 5, back.label()
    assert not [r.label() for r in result.rows
                if r.kind != "silence" and r.lane != "emergency" and r.start < end - 1]
    assert not result.failed(SILENCE), [f.text for f in result.failed(SILENCE)]
    # a run of Jerusalem alone must not draw Main's sponsorships (or loose copies) as filler
    assert not result.failed("nothing from another station")


def test_window_times_use_local_dates():
    w = _named("jerusalem", "Yom Kippur")
    local = w["start"].astimezone(ZoneInfo("Asia/Jerusalem"))
    assert (local.year, local.month, local.day, local.weekday()) == (2027, 10, 10, 6)
    assert datetime.fromisoformat("2027-10-10T17:00+03:00") < w["start"] < datetime.fromisoformat("2027-10-10T18:00+03:00")
