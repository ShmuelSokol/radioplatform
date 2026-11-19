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
@pytest.mark.xfail(strict=True, reason=(
    "Reproduction (2026-11-19 simulator): sponsorship breaks at :15/:30/:45/:00 are queued "
    "inside the 8 PM Daf Yomi when the shiur's block is queued in the same pass as the breaks "
    "(talk_windows only sees shiurim already in the queue). Reported to the sponsorship thread."))
async def test_no_sponsorship_inside_the_daf_yomi(tmp_path):
    result = await _run(tmp_path, datetime(2026, 11, 18, 19, 58, tzinfo=NY), 0.35, ["main"])
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


# "Time to Say Good Shabbos" (Lev Tahor) as measured on the PC 2026-11-19: music from
# 0.23 s, silence from 276.52 s to the end of the 279.48 s file
GOOD_SHABBOS = {
    "id": "e1ed49c2-10ad-411e-b58e-95c51d048a8e", "title": "Time to Say Good Shabbos",
    "asset_type": "music", "category": "top_hits", "duration": 279.48,
    "analysis": {"version": 2, "cue_in_seconds": 0.23, "cue_out_seconds": 276.52},
}
SHOFAR = {"id": "5f0f0a00-0000-4000-8000-00000000500f", "title": "Shofar (sim stand-in)",
          "asset_type": "jingle", "category": "shabbos_ending", "duration": 21.0}


def _ending(after: bool = True) -> dict:
    return {"shabbos_ending": {"enabled": True, "song_asset_id": GOOD_SHABBOS["id"],
                               "after_asset_id": SHOFAR["id"] if after else None}}


@pytest.mark.asyncio
@pytest.mark.parametrize("station,start", [
    ("main", datetime(2026, 11, 20, 15, 55, tzinfo=NY)),
    # KBR Jerusalem, windows made again (candle lighting 3:57 PM Jerusalem = 8:57 AM ET)
    ("jerusalem", datetime(2026, 11, 20, 8, 30, tzinfo=NY)),
])
async def test_good_shabbos_song_ends_at_candle_lighting_then_shofar(tmp_path, station, start):
    """Erev Shabbos (Shmuel 2026-11-19): the song ends exactly at candle lighting, the
    shofar starts right then, then only silence; no time check, sponsorship or station ID
    in between; the station's window is still candle lighting to shekia + 72."""
    from tests.sim.run import PICK

    sid = PICK[station]
    _, result = await simulate(EXPORT, start, 0.6, [station], tmp_path, overrides={sid: _ending()},
                               extra_assets=[GOOD_SHABBOS, SHOFAR], regenerate_windows=True)
    print(result.report())
    for check in ("Erev Shabbos: song ends at candle lighting, shofar, then silence",
                  "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"):
        assert result.checks_run[check] >= 1
        assert not result.failed(check), _failing(result, check)
    # The real engine stays quiet (its hold lane, playout_engine.HOLD_BEFORE_HARD_SECONDS)
    # when the song on air ends under 8 s before a hard item; the stand-in engine has no
    # hold lane and logs those seconds as emergency music. Only that case is let through.
    dead = [f for f in result.failed("no dead air or emergency music")
            if not (f.text.startswith("emergency music for") and float(f.text.split()[3]) < 8.0)]
    assert not dead, [f"{f.at}: {f.text}" for f in dead]
    assert not result.failed("no queue entry airs twice"), _failing(result, "no queue entry airs twice")


@pytest.mark.asyncio
async def test_good_shabbos_song_without_a_shofar(tmp_path):
    """Until there is a shofar recording: the song ends at candle lighting and the silence
    starts right after it."""
    from tests.sim.run import PICK

    _, result = await simulate(EXPORT, datetime(2026, 11, 20, 15, 55, tzinfo=NY), 0.6, ["main"], tmp_path,
                               overrides={PICK["main"]: _ending(after=False)}, extra_assets=[GOOD_SHABBOS])
    print(result.report())
    for check in ("Erev Shabbos: song ends at candle lighting, shofar, then silence",
                  "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"):
        assert result.checks_run[check] >= 1
        assert not result.failed(check), _failing(result, check)
