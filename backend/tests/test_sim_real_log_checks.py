"""Real play-log checks: station-ID rule follows placement; a doubled spot is caught."""
from datetime import UTC, datetime, timedelta

from tests.sim.playlog import check_export

CHECK = "station IDs spaced, own, clear of breaks"
SID = "11111111-0000-4000-8000-000000000001"


def _export(placement):
    ids = {"enabled": True, "category": "station_id", "min_minutes": 12, "max_minutes": 20}
    if placement:
        ids["placement"] = placement
    assets = [{"id": "song", "title": "Song", "asset_type": "music", "category": "top_hits", "duration": 240.0},
              {"id": "id", "title": "Station ID", "asset_type": "jingle", "category": "station_id", "duration": 8.0}]
    t = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
    logs = []
    for n in range(2 * 15):  # two hours: an ID once an hour, songs between
        aid, length = ("id", 8.0) if n % 15 == 0 else ("song", 240.0)
        logs.append({"station_id": SID, "asset_id": aid, "start_utc": t.isoformat(),
                     "end_utc": (t + timedelta(seconds=length)).isoformat()})
        t += timedelta(seconds=length)
    return {"stations": [{"id": SID, "name": "Test", "timezone": "America/New_York",
                          "automation_config": {"station_ids": ids}}],
            "assets": assets, "play_logs": logs}


def _failing(placement):
    res = check_export(_export(placement))
    return [f for f in res.findings if f.check == CHECK]


def test_hourly_ids_are_not_held_to_between_song_spacing():
    assert _failing(None) == []        # no placement key = hourly, the default
    assert _failing("hourly") == []


def test_between_songs_ids_an_hour_apart_fail_the_spacing():
    found = _failing("between_songs")
    assert found and all("min apart" in f.text for f in found)


def test_a_spot_logged_at_twice_its_length_is_a_double_play():
    """The engine played one sponsorship twice back to back and the log merged the plays
    into one row (Main 2026-11-20 14:19Z): the real-log check reports it."""
    export = _export(None)
    export["assets"].append({"id": "spot", "title": "Spot", "asset_type": "spot", "category": "sponsorship",
                             "duration": 21.8})
    last = export["play_logs"][-1]
    t = datetime.fromisoformat(last["end_utc"])
    export["play_logs"].append({"station_id": SID, "asset_id": "spot", "start_utc": t.isoformat(),
                                "end_utc": (t + timedelta(seconds=42.6)).isoformat()})
    res = check_export(export)
    found = [f for f in res.findings if f.check == "spoken item not played twice in a row"]
    assert len(found) == 1 and "played twice" in found[0].text


def _shiur_export(resume: bool):
    """KBR Jerusalem 2026-11-23 5:30 AM: the Daf Yomi paused for the :30 time check and,
    with resume, carried on to its end; without, the next song followed."""
    export = _export(None)
    export["assets"] += [
        {"id": "daf", "title": "Succah 50", "asset_type": "daf_yomi", "category": "daf_yomi", "duration": 1500.0},
        {"id": "tc", "title": "Time Announcement - 05:30", "asset_type": "time", "category": "time_announcement",
         "duration": 6.0}]
    t = datetime.fromisoformat(export["play_logs"][-1]["end_utc"])
    rows = [("daf", 1260.0), ("tc", 6.0)]
    rows += [("daf", 240.0)] if resume else []
    rows += [("song", 240.0), ("song", 240.0)]
    for aid, length in rows:
        export["play_logs"].append({"station_id": SID, "asset_id": aid, "start_utc": t.isoformat(),
                                    "end_utc": (t + timedelta(seconds=length)).isoformat()})
        t += timedelta(seconds=length)
    return export


def test_a_paused_shiur_that_never_resumed_is_found():
    res = check_export(_shiur_export(resume=False))
    found = [f for f in res.findings if f.check == "paused shiur or story resumed and reached its end"]
    assert len(found) == 1 and "never resumed" in found[0].text and "4.0 min left" in found[0].text


def test_a_paused_shiur_that_resumed_to_its_end_passes():
    res = check_export(_shiur_export(resume=True))
    assert res.checks_run["paused shiur or story resumed and reached its end"] == 1
    assert not [f for f in res.findings if f.check == "paused shiur or story resumed and reached its end"]


def test_a_shiur_that_may_have_begun_before_the_log_is_not_judged():
    """2026-11-23 export: the log starts at 12:00Z, the Daf began at 11:53Z; its parts in the
    log add up short only because the first part is missing."""
    export = _shiur_export(resume=False)
    first_daf = next(i for i, p in enumerate(export["play_logs"]) if p["asset_id"] == "daf")
    export["play_logs"] = export["play_logs"][first_daf:]
    res = check_export(export)
    assert not [f for f in res.findings if f.check == "paused shiur or story resumed and reached its end"]
