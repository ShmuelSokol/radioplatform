"""Station simulator scenarios (tests/sim, wiki/station-simulator.md).

The real scheduler pass, playout-engine link, queue fill, program blocks and sponsorship
planner run on a virtual clock against the stations' real setup (a trimmed export of
production, tests/sim/fixtures), with a stand-in for the stream engine. A broadcast hour
takes about a minute, so only short windows run here; whole days and weeks run with

    cd backend && uv run python -m tests.sim.run --export tests/sim/fixtures/stations-2026-11-19.json \
        --start 2026-11-19T00:00 --hours 24 --stations main

or here with SIM_LONG=1. Every check is listed in tests/sim/checks.py.
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.sim.run import simulate

NY = ZoneInfo("America/New_York")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
LONG = os.environ.get("SIM_LONG") == "1"


async def _run(tmp_path, start, hours, stations):
    _, result = await simulate(EXPORT, start, hours, stations, tmp_path)
    print(result.report())
    return result


def _failing(result, check):
    return [f"{f.at}: {f.text}" for f in result.failed(check)]


@pytest.mark.asyncio
async def test_main_top_of_the_hour_into_the_daf_yomi(tmp_path):
    """Main, Wednesday 2026-11-18 7:50 to 8:20 PM ET: the 8 PM time check, weather and block
    on their second, then the Daf Yomi; no dead air, IDs and sponsors apart, no other
    station's items."""
    result = await _run(tmp_path, datetime(2026, 11, 18, 19, 50, tzinfo=NY), 0.5, ["main"])
    for check in ("hard items start on their second", "time check at :00 and :30 within 1 s",
                  "no dead air or emergency music", "time check says the right time",
                  "same sponsor never twice within 20 min", "no queue entry airs twice"):
        assert not result.failed(check), _failing(result, check)
    assert result.checks_run["time check at :00 and :30 within 1 s"] >= 1
    assert any(r.kind == "daf_yomi" for r in result.rows)


@pytest.mark.asyncio
async def test_no_sponsorship_inside_the_daf_yomi(tmp_path):
    """Main 7:40 to 8:46 PM: the Daf block is queued in the same pass as the sponsor breaks
    around it; none may land inside it (PR 406: the blocks' own shiurim count)."""
    result = await _run(tmp_path, datetime(2026, 11, 18, 19, 40, tzinfo=NY), 1.1, ["main"])
    assert result.checks_run["no sponsorship inside a shiur or story"] >= 1
    assert not result.failed("no sponsorship inside a shiur or story")


@pytest.mark.asyncio
async def test_shabbos_starts_with_silence_at_candle_lighting(tmp_path):
    """Main, Friday 2026-11-20 around candle lighting (4:18 PM ET): the item on air fades,
    only the silence item airs; and the window itself is candle lighting to 72 min after
    shekia (Shmuel 2026-11-19)."""
    result = await _run(tmp_path, datetime(2026, 11, 20, 16, 5, tzinfo=NY), 0.4, ["main"])
    check = "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"
    assert result.checks_run[check] >= 1
    assert not result.failed(check), _failing(result, check)
    assert not result.failed("no dead air or emergency music"), _failing(result, "no dead air or emergency music")


@pytest.mark.skipif(not LONG, reason="a whole day per station: SIM_LONG=1")
@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["main", "jerusalem", "stories"])
async def test_a_whole_day(tmp_path, station):
    result = await _run(tmp_path, datetime(2026, 11, 19, 0, 0, tzinfo=NY), 24, [station])
    assert not result.failed(), result.report()


@pytest.mark.asyncio
async def test_sponsorships_wait_for_stories_on_all_day_stories(tmp_path):
    """All Day Stories, Thursday 2026-11-19 10 AM to 12:45 PM ET (coordinator 2026-11-19): with
    sponsorships_wait_for_talks a story is never cut or paused for a sponsorship; they play
    between stories. (Without the setting they cut in; tests/test_soft_items_pause_shiur.py
    covers both ways quickly.)"""
    from tests.sim.stations import STORIES

    overrides = {STORIES: {"sponsorships_wait_for_talks": True}}
    _, result = await simulate(EXPORT, datetime(2026, 11, 19, 10, 0, tzinfo=NY), 2.75, ["stories"], tmp_path,
                               overrides=overrides)
    print(result.report())
    check = "no sponsorship inside a shiur or story"
    assert result.checks_run[check] >= 1
    # only the sponsorship findings (a story cut by the hourly time check is the
    # engine's rule, not this one)
    sponsor_cuts = [f for f in _failing(result, check) if "sponsorship" in f]
    assert not sponsor_cuts, sponsor_cuts
    assert not result.failed("same sponsor never twice within 20 min"), _failing(result, "same sponsor never twice within 20 min")
    assert any(r.source == "ad_slot" for r in result.rows), "no sponsorship aired at all"
