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


# Main's real instrumental pool (main-1079/instrumental.json, 2026-11-25): 46 tracks of
# 3 to 12 minutes, 33 of them by one artist.
NIGHT_POOL = [["8th Day", 282.1], ["C. Lanzbom", 480.6], ["C. Lanzbom", 365.2], ["C. Lanzbom", 274.4], ["C. Lanzbom", 292.4], ["C. Lanzbom", 408.2], ["C. Lanzbom", 229.1], ["C. Lanzbom", 409.6], ["C. Lanzbom", 317.3], ["Eli Gerstner, The Yosis Orchestra", 658.3], ["Keola Beamer", 193.7], ["Keola Beamer", 291.2], ["Keola Beamer", 209.0], ["Tony O'Connor", 435.5], ["Tony O'Connor", 466.2], ["Tony O'Connor", 602.4], ["Tony O'Connor", 593.9], ["Tony O'Connor", 266.6], ["Tony O'Connor", 400.3], ["Tony O'Connor", 315.7], ["Tony O'Connor", 303.7], ["Tony O'Connor", 446.4], ["Tony O'Connor", 431.8], ["Tony O'Connor", 659.9], ["Tony O'Connor", 526.8], ["Tony O'Connor", 248.6], ["Tony O'Connor", 603.0], ["Tony O'Connor", 307.2], ["Tony O'Connor", 709.8], ["Tony O'Connor", 302.4], ["Tony O'Connor", 335.8], ["Tony O'Connor", 535.4], ["Tony O'Connor", 357.3], ["Tony O'Connor", 408.5], ["Tony O'Connor", 578.2], ["Tony O'Connor", 661.6], ["Tony O'Connor", 457.5], ["Tony O'Connor", 600.1], ["Tony O'Connor", 409.6], ["Tony O'Connor", 694.9], ["Tony O'Connor", 501.8], ["Tony O'Connor", 335.5], ["Tony O'Connor", 497.9], ["Tony O'Connor", 429.0], ["Tony O'Connor", 722.9], ["Tony O'Connor", 626.1]]


def _night_export(tmp_path: Path, pool: list | None = None) -> Path:
    """The export with Main's nights as set up on 2026-11-22 (apply_main_nights.py):
    51 instrumental tracks filed under relax and also tagged "instrumental", a Night
    Chill playlist of relax songs, the two new blocks one priority above Night Chill,
    and a Chanukah list in ordinary categories."""
    export = json.loads(EXPORT.read_text())
    stamp = "2026-11-22T15:00:00+00:00"

    def asset(title, category, seconds, artist=None):
        a = {"id": str(uuid.uuid4()), "title": title, "asset_type": "music", "category": category,
             "duration": seconds, "cues": {}, "artist": artist}
        export["assets"].append(a)
        return a

    if pool is None:
        instrumental = [asset(f"INSTR {i}", "relax", 240.0 + (i % 7) * 25) for i in range(51)]
    else:
        instrumental = [asset(f"INSTR {i}", "relax", seconds, artist) for i, (artist, seconds) in enumerate(pool)]
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
    # The song on air at 12:30 finishes, then the soft sponsorships; every song that
    # starts after them is Carlebach (two Night Chill songs aired there before
    # hour_fit.drop_songs_of_ended_blocks)
    carlebach = between(0, 31, 1, 0)
    assert len(carlebach) >= 4
    assert all(r.title.startswith("CARLEBACH") for r in carlebach), [r.title for r in carlebach]
    assert not any(r.title.startswith("CARLEBACH") for r in between(1, 0, 3, 0)), "Carlebach after 1 AM"

    night = between(3, 1, 6, 0)
    assert len(night) >= 30, len(night)  # the whole three hours (about 11 long tracks an hour), not the first
    wrong = [f"{_local(r.start):%H:%M:%S} {r.title}" for r in night if not r.title.startswith("INSTR")]
    assert not wrong, wrong
    # every hour of it
    for hour in (3, 4, 5):
        assert len(between(hour, 1, hour + 1, 0)) >= 8, hour  # about 11, fewer when the fit swaps in longer songs
    assert not any(r.title.startswith("HOLIDAY") for r in between(0, 31, 6, 0))
    assert not any(r.title.startswith("INSTR") for r in between(6, 2, 7, 0)), "instrumental after 6 AM"


async def test_main_nights_with_the_real_instrumentals(tmp_path):
    """The same night with Main's real 46 instrumentals (live 2026-11-25, after PR 507):
    the Gerstner Medley aired at 3:22, 4:36 and 5:50 and Sweet Lei Mamo twice in 34
    minutes, as the artist rule (2 songs) kept asking for the 13 tracks not by Tony
    O'Connor; and with tracks of 3 to 12 minutes no order of the waiting songs ended
    near the hour (12 s lost at 5:00, 70 s at 6:00)."""
    export = _night_export(tmp_path, NIGHT_POOL)
    length = {a["title"]: a["duration"] for a in json.loads(export.read_text())["assets"]}
    _, result = await simulate(export, datetime(2026, 11, 23, 2, 30, tzinfo=NY), 3.7, ["main"],
                               tmp_path, quiet_pass=12.0)
    print(result.report())
    songs = [r for r in result.rows if r.station == PICK["main"] and r.kind == "music" and r.lane != "timed"]
    for r in songs:
        print(f"  {_local(r.start):%H:%M:%S} {r.title}")
    a = datetime(2026, 11, 23, 3, 1, tzinfo=NY).timestamp()
    b = datetime(2026, 11, 23, 6, 0, tzinfo=NY).timestamp()
    night = [r for r in songs if a <= r.start < b and r.title.startswith("INSTR")]
    assert len(night) >= 20, len(night)
    # No track twice: the pool (46 tracks, 5.6 hours) is longer than the block
    titles = [r.title for r in night]
    twice = sorted({t for t in titles if titles.count(t) > 1})
    assert not twice, twice
    # Within the block the hard break on the hour loses little of the song on air
    # (a reorder, or songs of the block's playlist that end on the hour). The 6 AM
    # break is printed, not checked: this export still has hard :30 breaks at night,
    # which Main no longer has, so the run-up to 6:00 is only the half hour after 5:30.
    for hour in (4, 5, 6):
        at = datetime(2026, 11, 23, hour, 0, tzinfo=NY).timestamp()
        for r in songs:
            if r.start < at <= r.end + 1 and r.cut:
                lost = (length.get(r.title) or 0) - (r.cue_in + (r.end - r.start))
                print(f"  {hour}:00 cut {r.title} after {r.end - r.start:.0f} s, {lost:.0f} s lost")
                if hour < 6:
                    assert lost <= 25, (hour, r.title, lost)
