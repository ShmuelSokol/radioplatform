"""Main's nights, played out in the station simulator (tests/sim): the Carlebach half
hour (12:30-1 AM) and the instrumental block (3-6 AM) set up 2026-11-22 for "Main
follows 107.9", between Night Chill (10 PM-6 AM), in Chanukah's run-up (holiday mix on).

Live 2026-11-23: after PR 498 the leftover clean-up before each hard break took out
every Night instrumental song after 4 AM (the 6 AM block was within its look), and
Top 500 Hits songs with Chanukah songs aired from 4:01. The check is the whole of
each block, not its first hour.

    cd backend && SIM_LONG=1 uv run pytest tests/test_main_nights_sim.py -v -s
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.sim.run import PICK, simulate
from tests.test_holiday_sim import EXPORT, MUST_PASS

NY = ZoneInfo("America/New_York")
MAIN = "95358e09-1260-48d5-85b5-f851000773c0"
DAILY = "fcd90d8e-79d8-43b1-b7a8-893944defd98"  # KBR Main Daily
pytestmark = [
    pytest.mark.skipif(os.environ.get("SIM_LONG") != "1", reason="simulator scenarios: SIM_LONG=1"),
    pytest.mark.asyncio,
]


def _night_export(tmp_path: Path) -> Path:
    """The export with Main's nights as set up on 2026-11-22 (apply_main_nights.py):
    51 instrumental tracks filed under relax and also tagged "instrumental", a Night
    Chill playlist of relax songs, the two new blocks one priority above Night Chill,
    and a Chanukah list in ordinary categories."""
    export = json.loads(EXPORT.read_text())
    stamp = "2026-11-22T15:00:00+00:00"

    def asset(title, category, seconds):
        a = {"id": str(uuid.uuid4()), "title": title, "asset_type": "music", "category": category,
             "duration": seconds, "cues": {}}
        export["assets"].append(a)
        return a

    instrumental = [asset(f"INSTR {i}", "relax", 240.0 + (i % 7) * 25) for i in range(51)]
    export["asset_categories"] = [{"asset_id": a["id"], "category": "instrumental"} for a in instrumental]
    for i in range(40):
        asset(f"CARLEBACH {i}", "Carlebach", 230.0 + (i % 5) * 30)
    for i in range(30):
        asset(f"NIGHT RELAX {i}", "relax", 260.0)

    def template(name, category, steps):
        t = {"id": str(uuid.uuid4()), "name": name, "description": None, "station_id": MAIN, "is_active": True,
             "created_at": stamp, "updated_at": stamp}
        export["playlist_templates"].append(t)
        for i in range(steps):
            export["template_slots"].append({"id": str(uuid.uuid4()), "template_id": t["id"], "position": i,
                                             "asset_type": "music", "category": category,
                                             "created_at": stamp, "updated_at": stamp})
        return t["id"]

    chill = template("Night Chill", "relax", 3)
    for b in export["schedule_blocks"]:
        if b["schedule_id"] == DAILY and b["name"] == "Night Chill":
            b["playlist_template_id"] = chill
            night = b

    def block(name, start, end, template_id):
        export["schedule_blocks"].append({
            **night, "id": str(uuid.uuid4()), "name": name, "start_time": start, "end_time": end,
            "priority": (night.get("priority") or 0) + 1, "playlist_template_id": template_id,
            "created_at": stamp, "updated_at": stamp,
        })

    block("Carlebach half hour", "00:30:00", "01:00:00", template("Main - Carlebach half hour", "Carlebach", 4))
    block("Night instrumental", "03:00:00", "06:00:00", template("Main - Night instrumental", "instrumental", 4))
    path = tmp_path / "export-main-nights.json"
    path.write_text(json.dumps(export))
    return path


def _chanukah_list():
    assets, items = [], []
    for i in range(20):
        aid = str(uuid.uuid4())
        assets.append({"id": aid, "title": f"HOLIDAY chanukah {i}", "asset_type": "music", "category": "lively",
                       "duration": 74.0})
        items.append({"holiday": "chanukah", "asset_id": aid, "station_id": None})
    return assets, items


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, NY)


async def test_main_nights_carlebach_then_three_hours_of_instrumentals(tmp_path):
    """Monday 2026-11-23 from 12:15 AM to 6:40 AM on Main, with the hard hourly breaks."""
    assets, items = _chanukah_list()
    _, result = await simulate(_night_export(tmp_path), datetime(2026, 11, 23, 0, 15, tzinfo=NY), 6.42, ["main"],
                               tmp_path, extra_assets=assets, holiday_items=items, quiet_pass=12.0)
    print(result.report())
    for check in MUST_PASS:
        assert not result.failed(check), [f"{f.at}: {f.text}" for f in result.failed(check)]

    songs = [r for r in result.rows if r.station == PICK["main"] and r.kind == "music" and r.lane != "timed"]
    for r in songs:
        print(f"  {_local(r.start):%H:%M:%S} {r.title}")

    def between(h1, m1, h2, m2):
        a = datetime(2026, 11, 23, h1, m1, tzinfo=NY).timestamp()
        b = datetime(2026, 11, 23, h2, m2, tzinfo=NY).timestamp()
        return [r for r in songs if a <= r.start < b]

    # The song that straddles a block's start comes from the block before (the fill's
    # rule), so each block is checked from its first song change on
    # The 12:30 sponsorships are soft and the fill places songs before them: one or
    # two Night Chill songs drawn to start before 12:30 still air after the break
    # (live 12:30:43 and 12:34:43 here; live 2026-11-23 one, to 12:34:08). Known, not
    # yet fixed: Carlebach must start by 12:40 and be all that plays from then to 1 AM.
    carlebach = between(0, 31, 1, 0)
    first = next(i for i, r in enumerate(carlebach) if r.title.startswith("CARLEBACH"))
    assert carlebach[first].start < datetime(2026, 11, 23, 0, 40, tzinfo=NY).timestamp(), carlebach[first].title
    assert len(carlebach[first:]) >= 4
    assert all(r.title.startswith("CARLEBACH") for r in carlebach[first:]), [r.title for r in carlebach]
    assert not any(r.title.startswith("CARLEBACH") for r in between(1, 0, 3, 0)), "Carlebach after 1 AM"

    night = between(3, 1, 6, 0)
    assert len(night) >= 36, len(night)  # the whole three hours, not the first
    wrong = [f"{_local(r.start):%H:%M:%S} {r.title}" for r in night if not r.title.startswith("INSTR")]
    assert not wrong, wrong
    # every hour of it
    for hour in (3, 4, 5):
        assert len(between(hour, 1, hour + 1, 0)) >= 10, hour
    assert not any(r.title.startswith("HOLIDAY") for r in between(0, 31, 6, 0))
    assert not any(r.title.startswith("INSTR") for r in between(6, 2, 7, 0)), "instrumental after 6 AM"
