"""Motzei Shabbos played out in the station simulator (wiki/song-categories.md).

When Shabbos ends (72 minutes after shekia) the station opens with Motzei Shabbos songs
(category motzei_shabbos, mix-only: they never come up in an "any song" step) for half an
hour, then plays the hot new mix for the rest of the night. Built as ordinary playlists
and weekly sun-timed blocks, as the live site gets them (holiday-music/shabbos script).

    cd backend && SIM_LONG=1 uv run pytest tests/test_motzei_shabbos_sim.py -v -s
"""
import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services.shabbos_service import generate_shabbos_windows
from tests.sim.checks import PLACES
from tests.sim.run import PICK, simulate

NY = ZoneInfo("America/New_York")
JLM = ZoneInfo("Asia/Jerusalem")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
SCHEDULE = {"main": "fcd90d8e-79d8-43b1-b7a8-893944defd98", "jerusalem": "f51c2696-c0e8-4871-964e-f5407a451894"}
OPENING_MINUTES = 30
pytestmark = [
    pytest.mark.skipif(os.environ.get("SIM_LONG") != "1", reason="simulator scenarios: SIM_LONG=1"),
    pytest.mark.asyncio,
]
MUST_PASS = (
    "hard items start on their second", "time check at :00 and :30 within 1 s",
    "no queue entry airs twice", "no dead air or emergency music", "nothing from another station",
    "site play log: one row per item aired, at its real time, none overlapping",
    "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia",
)


def _export_with_motzei_shabbos(tmp_path: Path, stations) -> tuple[Path, set[str]]:
    """The export plus Motzei Shabbos songs, the two playlists and the two Saturday blocks
    on each station, as apply_shabbos_playlists.py makes them."""
    d = json.loads(EXPORT.read_text())
    motzei = set()
    for i in range(4):
        aid = str(uuid.uuid4())
        motzei.add(aid)
        d["assets"].append({"id": aid, "title": f"MOTZEI Hamavdil {i}", "asset_type": "music",
                            "category": "motzei_shabbos", "duration": 230.0})
    sid_of = {s: PICK[s] for s in stations}
    now = "2026-11-19T00:00:00+00:00"
    for name in stations:
        sid = sid_of[name]
        for tname, steps, start, end in (
            ("Motzei Shabbos opening", ["motzei_shabbos", "hot_now", "motzei_shabbos", "top_hits"],
             ("SUNSET", 72), ("SUNSET", 72 + OPENING_MINUTES)),
            ("Motzei Shabbos hot mix", ["hot_now", "top_hits", "top_hits", None, "hot_now", "top_hits", "top_hits"],
             ("SUNSET", 72 + OPENING_MINUTES), None),
        ):
            tid = str(uuid.uuid4())
            d["playlist_templates"].append({"id": tid, "name": tname, "description": None, "station_id": sid,
                                            "is_active": True, "created_at": now, "updated_at": now})
            for pos, cat in enumerate(steps):
                d["template_slots"].append({"id": str(uuid.uuid4()), "template_id": tid, "position": pos,
                                            "asset_type": "jingle" if cat is None else "music", "category": cat,
                                            "created_at": now, "updated_at": now})
            d["schedule_blocks"].append({
                "id": str(uuid.uuid4()), "schedule_id": SCHEDULE[name], "name": tname, "description": None,
                "start_time": "18:00:00", "end_time": "23:59:59", "recurrence_type": "WEEKLY",
                "recurrence_pattern": ["saturday"], "priority": 0, "playlist_template_id": tid,
                "playback_mode": "SEQUENTIAL", "start_date": None, "end_date": None,
                "start_sun_event": start[0], "start_sun_offset": start[1],
                "end_sun_event": end[0] if end else None, "end_sun_offset": end[1] if end else None,
                "until_next": False, "created_at": now, "updated_at": now})
    path = tmp_path / "export-motzei.json"
    path.write_text(json.dumps(d))
    hot = {a["title"] for a in d["assets"] if a["category"] == "hot_now"}
    hits = {a["title"] for a in d["assets"] if a["category"] == "top_hits"}
    return path, (hot, hits)


async def _run(tmp_path, station, tz, also=()):
    export, hot = _export_with_motzei_shabbos(tmp_path, (station, *also))
    start = datetime(2026, 11, 21, 15, 30, tzinfo=tz)
    _, result = await simulate(export, start, 4.0, [station, *also], tmp_path, quiet_pass=12.0,
                               regenerate_windows=True)
    print(result.report())
    for check in MUST_PASS:
        assert not result.failed(check), [f"{f.at}: {f.text}" for f in result.failed(check)]
    _, lat, lon, _ = PLACES[str(tz)]
    w = generate_shabbos_windows(lat, lon, str(tz), date(2026, 11, 20), date(2026, 11, 20), ["s"])[0]
    end = datetime.fromisoformat(w["end_datetime"]).timestamp()
    rows = [r for r in result.rows if r.station == PICK[station] and r.kind == "music" and r.lane != "timed"]
    after = [r for r in rows if r.start >= end - 1]
    print(f"Shabbos ends {datetime.fromtimestamp(end, tz):%H:%M:%S}; songs after:")
    for r in [r for r in result.rows if r.station == PICK[station] and end - 2400 <= r.start <= end + 1500]:
        print(f"  {r.local_start} {r.lane} {r.kind} {r.title}")
    return rows, after, end, hot


def _check(rows, after, end, hot):
    motzei = [r for r in rows if r.title.startswith("MOTZEI")]
    assert after, "nothing played after Shabbos"
    # the first song back is a Motzei Shabbos song, then about every second one for half an hour
    assert after[0].title.startswith("MOTZEI"), after[0].title
    opening = [r for r in after if r.start < end + OPENING_MINUTES * 60]
    assert sum(r.title.startswith("MOTZEI") for r in opening) >= 3, [r.title for r in opening]
    # never during Shabbos's run-up, never in the hot mix after the opening
    assert all(end - 1 <= r.start < end + (OPENING_MINUTES + 5) * 60 for r in motzei), \
        [(r.local_start, r.title) for r in motzei]
    # the hot new mix after the opening: hot new songs and top hits (only 7 hot new songs
    # exist, and they rest between plays, so the share of hot ones varies)
    hot, hits = hot
    later = [r for r in after if r.start >= end + (OPENING_MINUTES + 5) * 60]
    assert later and sum(r.title in hot for r in later) >= 2, [r.title for r in later]
    assert sum(r.title in hot or r.title in hits for r in later) >= len(later) * 0.8, [r.title for r in later]


async def test_main_motzei_shabbos(tmp_path):
    """Saturday Nov 21, 2026 in Lakewood: Shabbos ends about 5:46 PM."""
    _check(*await _run(tmp_path, "main", NY))


async def test_jerusalem_motzei_shabbos(tmp_path):
    """Saturday Nov 21, 2026 in Jerusalem, with Main seeded beside it (see test_holiday_sim)."""
    _check(*await _run(tmp_path, "jerusalem", JLM, also=("main",)))


def test_the_export_change_is_well_formed(tmp_path):
    path, (hot, hits) = _export_with_motzei_shabbos(tmp_path, ("main",))
    d = json.loads(path.read_text())
    assert len(hot) >= 5 and len(hits) > 20
    assert sum(1 for b in d["schedule_blocks"] if b["recurrence_pattern"] == ["saturday"]) == 2
    assert timedelta(minutes=OPENING_MINUTES)
