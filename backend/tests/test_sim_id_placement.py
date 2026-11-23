"""The station-ID rule check follows the station's placement: spacing only between songs."""
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
