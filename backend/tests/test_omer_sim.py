"""Sefiras HaOmer played out in the station simulator (tests/sim, wiki/omer-count.md).

tests/test_omer_count.py plans the whole 49 days for Lakewood and Jerusalem in seconds;
these play hours of air on the real scheduling code with the simulated engine around the
moments that matter: the switch at shekia in each city, into and out of Shabbos, the
first night after Pesach, Lag BaOmer and Erev Shavuos. The simulator's own check
("Omer count after each hour's time check, the right day") looks at every hh:00.

    cd backend && SIM_LONG=1 uv run pytest tests/test_omer_sim.py -v -s
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.sim.run import PICK, simulate

NY = ZoneInfo("America/New_York")
JLM = ZoneInfo("Asia/Jerusalem")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
pytestmark = [
    pytest.mark.skipif(os.environ.get("SIM_LONG") != "1", reason="simulator scenarios: SIM_LONG=1"),
    pytest.mark.asyncio,
]
OMER = "Omer count after each hour's time check, the right day"
MUST_PASS = (
    OMER, "hard items start on their second", "time check at :00 and :30 within 1 s", "time check says the right time",
    "no queue entry airs twice", "no dead air or emergency music", "nothing from another station",
    "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia",
)


async def _run(tmp_path, start, hours, stations, overrides=None):
    _, result = await simulate(EXPORT, start, hours, list(stations), tmp_path, quiet_pass=12.0,
                               regenerate_windows=True, overrides=overrides)
    print(result.report())
    for check in MUST_PASS:
        assert not result.failed(check), [f"{f.at}: {f.text}" for f in result.failed(check)]
    out = {}
    for name in stations:
        rows = [r for r in result.rows if r.station == PICK[name] and r.category == "omer_count"]
        out[name] = [(r.local_start[:9], r.title) for r in rows]
        print(name, out[name])
    return result, out


async def test_weekday_switch_at_shekia_lakewood(tmp_path):
    """Monday May 3, 2027 (day 11): shekia 7:53 PM in Lakewood. 5, 6, 7 PM say day 11,
    8 and 9 PM day 12 ("Today is the twelfth day"), on Main and All Day Stories alike."""
    _, said = await _run(tmp_path, datetime(2027, 5, 3, 16, 30, tzinfo=NY), 5, ("main", "stories"))
    for name in ("main", "stories"):
        assert said[name] == [("Mon 17:00", "Omer day 11"), ("Mon 18:00", "Omer day 11"),
                              ("Mon 19:00", "Omer day 11"), ("Mon 20:00", "Omer day 12"),
                              ("Mon 21:00", "Omer day 12")], said[name]


async def test_weekday_switch_at_shekia_jerusalem(tmp_path):
    """Monday May 3, 2027 in Jerusalem: shekia 7:20 PM, so 7 PM says day 11 and 8 PM day 12."""
    _, said = await _run(tmp_path, datetime(2027, 5, 3, 17, 30, tzinfo=JLM), 4, ("jerusalem", "main"))
    assert [t for _, t in said["jerusalem"]] == ["Omer day 11", "Omer day 11", "Omer day 12", "Omer day 12"]
    assert said["jerusalem"][2][0] == "Mon 20:00"


async def test_later_switch_setting(tmp_path):
    """The same evening with the switch 50 minutes after shekia (8:43 PM): 8 PM still
    says day 11."""
    from tests.sim.stations import MAIN

    _, said = await _run(tmp_path, datetime(2027, 5, 3, 18, 30, tzinfo=NY), 3, ("main",),
                         overrides={MAIN: {"omer_count": {"enabled": True, "switch_minutes": 50}}})
    assert [t for _, t in said["main"]] == ["Omer day 11", "Omer day 11", "Omer day 12"]


async def test_setting_off(tmp_path):
    from tests.sim.stations import MAIN

    _, said = await _run(tmp_path, datetime(2027, 5, 3, 18, 30, tzinfo=NY), 2, ("main",),
                         overrides={MAIN: {"omer_count": {"enabled": False}}})
    assert said["main"] == []


async def test_erev_shabbos_and_motzei_shabbos(tmp_path):
    """Friday May 7, 2027 (day 15): counts until 7 PM, nothing from candle lighting
    (7:41 PM) through Shabbos; Saturday night after 9:12 PM: 10 and 11 PM say day 17."""
    _, said = await _run(tmp_path, datetime(2027, 5, 7, 16, 30, tzinfo=NY), 31, ("main",))
    assert said["main"][:3] == [("Fri 17:00", "Omer day 15"), ("Fri 18:00", "Omer day 15"),
                                ("Fri 19:00", "Omer day 15")]
    assert said["main"][3:] == [("Sat 22:00", "Omer day 17"), ("Sat 23:00", "Omer day 17")]


async def test_first_night_after_pesach(tmp_path):
    """Jerusalem keeps one day Yom Tov: Thursday April 22, 2027 it ends about 8:20 PM and
    9 PM says day 1; Friday says day 1 until candle lighting (6:32 PM). Lakewood's two days
    run into Shabbos; its first count is Motzei Shabbos April 24 at 9 PM, day 3."""
    _, jlm = await _run(tmp_path, datetime(2027, 4, 22, 19, 0, tzinfo=JLM), 24, ("jerusalem", "main"))
    assert jlm["jerusalem"][0] == ("Thu 21:00", "Omer day 1")
    assert jlm["jerusalem"][-1] == ("Fri 18:00", "Omer day 1"), jlm["jerusalem"]
    _, ny = await _run(tmp_path, datetime(2027, 4, 24, 19, 30, tzinfo=NY), 3.5, ("main",))
    assert ny["main"][0] == ("Sat 21:00", "Omer day 3"), ny["main"]


async def test_lag_baomer(tmp_path):
    """Monday May 24, 2027 at 9 PM in Lakewood: Lag BaOmer (day 33) has begun."""
    _, said = await _run(tmp_path, datetime(2027, 5, 24, 18, 30, tzinfo=NY), 3, ("main",))
    assert said["main"][-1] == ("Mon 21:00", "Omer day 33")


async def test_erev_shavuos(tmp_path):
    """Thursday June 10, 2027: day 49 until candle lighting, then Shavuos silence and no
    day 50, in both cities."""
    _, ny = await _run(tmp_path, datetime(2027, 6, 10, 16, 30, tzinfo=NY), 5, ("main",))
    assert ny["main"] and {t for _, t in ny["main"]} == {"Omer day 49"}
    assert ny["main"][-1][0] == "Thu 20:00"  # candles 8:12 PM
    _, jlm = await _run(tmp_path, datetime(2027, 6, 10, 15, 30, tzinfo=JLM), 5, ("jerusalem", "main"))
    assert jlm["jerusalem"] and {t for _, t in jlm["jerusalem"]} == {"Omer day 49"}
    assert jlm["jerusalem"][-1][0] == "Thu 19:00"  # candles 7:07 PM
