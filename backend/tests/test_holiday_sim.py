"""Holiday music played out in the station simulator (tests/sim, wiki/holiday-music.md).

tests/test_holiday_year.py checks the plan for every day of the year in seconds; these
play hours of air on the real scheduling code with the simulated engine around the
moments that matter, and hold the station's own rules: time checks on the second,
sponsorships, the silence from candle lighting, nothing overlapping, no item twice.

    cd backend && SIM_LONG=1 uv run pytest tests/test_holiday_sim.py -v -s
"""
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services.shabbos_service import generate_shabbos_windows
from tests.sim.run import simulate
from tests.sim.stations import STORIES

NY = ZoneInfo("America/New_York")
JLM = ZoneInfo("Asia/Jerusalem")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
pytestmark = [
    pytest.mark.skipif(os.environ.get("SIM_LONG") != "1", reason="simulator scenarios: SIM_LONG=1"),
    pytest.mark.asyncio,
]
MUST_PASS = (
    "hard items start on their second", "time check at :00 and :30 within 1 s", "time check says the right time",
    "no queue entry airs twice", "no dead air or emergency music", "nothing from another station",
    "site play log: one row per item aired, at its real time, none overlapping",
    "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia",
)


def _lists(holiday: str, songs: int = 12, shiurim: int = 2, stories: int = 4, station_id: str | None = None):
    assets, items = [], []
    for kind, n, seconds in (("music", songs, 225.0), ("shiur", shiurim, 1500.0), ("stories", stories, 1100.0)):
        for i in range(n):
            aid = str(uuid.uuid4())
            assets.append({"id": aid, "title": f"HOLIDAY {holiday} {kind} {i}", "asset_type": kind,
                           "category": "lively" if kind == "music" else None, "duration": seconds})
            items.append({"holiday": holiday, "asset_id": aid, "station_id": station_id})
    return assets, items


async def _run(tmp_path, start, hours, station, holiday, also=(), **kw):
    """`also`: stations seeded beside it. Seeded alone, KBR Jerusalem draws Main's test
    sponsorship recordings as spots (their contracts are not loaded, so nothing marks
    them as sponsorship audio) and fails "nothing from another station"."""
    assets, items = _lists(holiday, **kw)
    _, result = await simulate(EXPORT, start, hours, [station, *also], tmp_path, extra_assets=assets,
                               holiday_items=items, quiet_pass=12.0, regenerate_windows=True)
    print(result.report())
    for check in MUST_PASS:
        assert not result.failed(check), [f"{f.at}: {f.text}" for f in result.failed(check)]
    from tests.sim.run import PICK

    sid = PICK[station]
    rows = [r for r in result.rows if r.station == sid]
    holiday_rows = [r for r in rows if r.title.startswith("HOLIDAY")]
    ordinary = [r for r in rows if r.kind in ("music", "shiur", "stories") and r.lane != "timed"]
    print(f"{len(holiday_rows)} holiday items of {len(ordinary)} songs, shiurim and stories:")
    for r in holiday_rows:
        print(f"  {r.local_start} {r.title}")
    return result, holiday_rows, ordinary


async def test_main_on_erev_chanukah_friday_peak_then_shabbos(tmp_path):
    """Friday Dec 4, 2026: the day before Chanukah (1 in 4) and Shabbos Chanukah at
    candle lighting. Holiday songs at about every fourth song until then, none in the
    silence, and the 1, 2, 3 PM time checks, sponsorships and the Shabbos ending as always."""
    result, got, ordinary = await _run(tmp_path, datetime(2026, 12, 4, 11, 0, tzinfo=NY), 6.5, "main", "chanukah")
    w = generate_shabbos_windows(40.0821, -74.2097, "America/New_York", date(2026, 12, 4), date(2026, 12, 4), ["s"])[0]
    candles = datetime.fromisoformat(w["start_datetime"]).timestamp()
    before = [r for r in ordinary if r.start < candles]
    assert abs(len(got) - len(before) / 4) <= 3, (len(got), len(before))
    assert all(r.start < candles for r in got)
    # never two holiday items in a row
    rows = [r for r in result.rows if r.kind in ("music", "shiur", "stories") and r.lane != "timed"]
    assert not any(a.title.startswith("HOLIDAY") and b.title.startswith("HOLIDAY") for a, b in zip(rows, rows[1:]))


async def test_main_after_chanukah_ends_none(tmp_path):
    """Monday Dec 14, 2026, two days after Chanukah's last day: no holiday items at all."""
    _, got, ordinary = await _run(tmp_path, datetime(2026, 12, 14, 10, 0, tzinfo=NY), 2, "main", "chanukah")
    assert got == [] and len(ordinary) > 20


async def test_jerusalem_during_chanukah(tmp_path):
    """Tuesday Dec 8, 2026 in Jerusalem: a Chanukah day on the air, 1 in 4."""
    _, got, ordinary = await _run(tmp_path, datetime(2026, 12, 8, 10, 0, tzinfo=JLM), 3, "jerusalem", "chanukah",
                              also=("main",))
    assert abs(len(got) - len(ordinary) / 4) <= 3, (len(got), len(ordinary))


async def test_stories_on_erev_pesach(tmp_path):
    """Wednesday Apr 21, 2027, Erev Pesach, on All Day Stories with a Pesach list for that
    station only (the 10 plagues): about every fourth story, stories only, never a song."""
    result, got, ordinary = await _run(tmp_path, datetime(2027, 4, 21, 9, 0, tzinfo=NY), 5, "stories", "pesach",
                                       songs=6, shiurim=0, stories=6, station_id=STORIES)
    assert got, "no Pesach story aired"
    assert all(" stories " in r.title for r in got)
    stories = [r for r in ordinary if r.kind == "stories"]
    assert abs(len(got) - len(stories) / 4) <= 3, (len(got), len(stories))
