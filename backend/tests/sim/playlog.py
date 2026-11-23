"""The same rule checks on a REAL play log: no simulation at all.

    cd backend && uv run python -m tests.sim.playlog /path/to/export.json

The export is what the PC thread writes from production (read only): `stations`, `assets`
(id, title, asset_type, category, duration), `spot_schedules`, `holiday_windows` and
`play_logs` rows of {station_id, asset_id, start_utc, end_utc} (the older key
`play_logs_main_0030_0215Z` is read as Main's). Checks that need the queue's timing
fields (hard/soft per item) are left out; the time check at :00 and :30 covers on-time.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tests.sim import checks
from tests.sim.stations import BLANKED, MAIN

_CLOCK = re.compile(r"(\d{1,2}):(\d{2})\s*$")
REAL_CHECKS = [checks.shabbos_silence, checks.time_checks_on_the_hour, checks.no_dead_air,
               checks.nothing_inside_a_shiur, checks.sponsor_spacing, checks.station_ids,
               checks.station_isolation, checks.time_check_says_the_hour]


def _dt(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _asset(a: dict):
    meta = {}
    if a.get("category") == "time_announcement":
        m = _CLOCK.search(a.get("title") or "")
        if m:  # "Time Announcement - 20:30"
            meta = {"time_of_day": f"{int(m.group(1)):02d}"}
            if m.group(2) == "30":
                meta["time_minute"] = "30"
    return SimpleNamespace(id=a["id"], title=a.get("title") or "", asset_type=a.get("asset_type") or "",
                           category=a.get("category"), duration=a.get("duration"), metadata_extra=meta)


# Items that are said, not sung: one of them twice in a row is a fault
SPOKEN_TYPES = {"spot", "jingle", "sweeper", "station_id", "time", "weather", "psa", "announcement", "zmanim"}
HOLE_SECONDS = 1800.0  # nothing logged for this long: the log was off, not dead air


# A shiur or story whose last part stops this far before its end did not finish
NOT_FINISHED_SECONDS = 120.0
RESUME_WITHIN_SECONDS = 3 * 3600


def _paused_items_finish(res, rows, assets, blackouts, stations):
    """Every shiur or story that stopped early came back and played to its end (KBR
    Jerusalem 2026-11-23 5:30 AM: the Daf Yomi paused for the time check never resumed and
    lost its last 4 minutes; the simulator's engine always resumes, so only the real log
    shows it). A part that stops at candle lighting, or is the last row of the log (still on
    air), is not a finding, nor one that may have begun before the log starts. An operator who
    skipped the item shows here too."""
    name = "paused shiur or story resumed and reached its end"
    res.checks_run.setdefault(name, 0)
    last_row, first_row = {}, {}
    for r in rows:
        last_row[r.station] = r
        first_row.setdefault(r.station, r)
    by_item = defaultdict(list)
    for r in rows:
        if r.is_long and r.asset_id:
            by_item[(r.station, r.asset_id)].append(r)
    for (sid, aid), parts in by_item.items():
        a = assets.get(aid)
        if a is None or not a.duration:
            continue
        # split into airings: a part more than 3 h after the last one is a new airing
        airings, cur = [], [parts[0]]
        for p in parts[1:]:
            if p.start - cur[-1].end < RESUME_WITHIN_SECONDS:
                cur.append(p)
            else:
                airings.append(cur)
                cur = [p]
        airings.append(cur)
        for airing in airings:
            final = airing[-1]
            if final is last_row.get(sid):
                continue
            # it may have begun before the log does: an earlier part is not in this export
            # (2026-11-23 export: the Daf from 11:53Z and a story from 11:56Z, log from 12:00Z)
            if airing[0].start - first_row[sid].start < float(a.duration):
                continue
            left = float(a.duration) - (final.cue_in + final.seconds)
            if len(airing) == 1 and left <= NOT_FINISHED_SECONDS:
                continue  # an ordinary airing, start to end
            res.checks_run[name] += 1
            if left <= NOT_FINISHED_SECONDS:
                continue
            at_candles = any(abs(w["start"].timestamp() - final.end) < 60 for w in blackouts
                             if not w["stations"] or sid in w["stations"])
            if at_candles:
                continue
            tz = ZoneInfo(stations[sid].timezone or "America/New_York")
            when = datetime.fromtimestamp(final.end, UTC).astimezone(tz).strftime("%a %H:%M:%S")
            how = "never resumed" if len(airing) == 1 else f"resumed {len(airing) - 1} time(s) but stopped again"
            res.findings.append(checks.Finding(
                name, sid, when, f"{a.title!r} stopped with {left / 60:.1f} min left and {how}"))


def check_export(export: dict, since: datetime | None = None, station: str | None = None) -> checks.Result:
    assets = {a["id"]: _asset(a) for a in export["assets"]}
    stations = {}
    for s in export["stations"]:
        config = {k: (BLANKED.get(k, True) if v == "<removed>" else v)
                  for k, v in (s.get("automation_config") or {}).items()}
        stations[s["id"]] = SimpleNamespace(id=s["id"], name=s["name"], timezone=s["timezone"],
                                            automation_config=config)
    spot_sponsor, spot_station = {}, defaultdict(set)
    for s in export.get("spot_schedules", []):
        spot_sponsor[s["asset_id"]] = s["sponsor_id"]
        spot_station[s["asset_id"]].add(s.get("station_id"))
    logs = list(export.get("play_logs") or [])
    for p in export.get("play_logs_main_0030_0215Z") or []:
        logs.append({**p, "station_id": MAIN})
    rows, doubled = [], []
    for p in logs:
        sid = p["station_id"]
        if sid not in stations or not p.get("end_utc") or (station and sid != station):
            continue
        if since is not None and _dt(p["start_utc"]) < since:
            continue
        a = assets.get(p.get("asset_id") or "")
        begun, ended = _dt(p["start_utc"]).timestamp(), _dt(p["end_utc"]).timestamp()
        if a is not None and a.duration:
            # A spoken item's row about twice its length: the engine played it twice back
            # to back and the log merged the plays into one row (Main TEST SPONSOR 45,
            # 2026-11-20 14:19Z: one entry handed twice, 29 s apart, logged as 42.6 s)
            if (a.asset_type in SPOKEN_TYPES and float(a.duration) >= 5.0
                    and ended - begun >= 1.8 * float(a.duration)):
                doubled.append((sid, begun, a.title, ended - begun, float(a.duration)))
            # a row closed late (after a restart) did not air past the item's own length
            ended = min(ended, begun + float(a.duration) + 5.0)
        rows.append(checks.Row(
            sid, None, "main", begun, ended, False, 0.0,
            a.title if a else "(unknown item)", kind=a.asset_type if a else "", category=a.category if a else None,
            asset_id=a.id if a else None, sponsor=checks._sponsor_key(a, spot_sponsor),
            tz=ZoneInfo(stations[sid].timezone or "America/New_York"),
        ))
    rows.sort(key=lambda r: (r.station, r.start))
    # A paused shiur shows as two rows of one asset: mark the first as cut, and give the
    # second its place in the recording so the "resumed where it stopped" check can run
    played: dict = {}
    for r in rows:
        if r.is_long and r.asset_id:
            key = (r.station, r.asset_id)
            prev = played.get(key)
            if prev is not None and r.start - prev.end < 3 * 3600:
                prev.cut = True
                r.cue_in = prev.cue_in + prev.seconds
            played[key] = r
    blackouts = []
    for h in export.get("holiday_windows", []):
        if h.get("is_blackout", True):
            blackouts.append({"start": _dt(h["start_datetime"]), "end": _dt(h["end_datetime"]),
                              "stations": set((h.get("affected_stations") or {}).get("station_ids") or []),
                              "reason": h.get("reason") or h.get("name")})
    start = min((r.start for r in rows), default=0.0)
    end = max((r.end for r in rows), default=0.0)
    holes = defaultdict(list)
    last: dict = {}
    for r in rows:
        prev = last.get(r.station)
        if prev is not None and r.start - prev.end > HOLE_SECONDS:
            holes[r.station].append((prev.end, r.start))
        last[r.station] = r
    sim = SimpleNamespace(start_dt=datetime.fromtimestamp(start, UTC), t=end, blackouts=blackouts,
                          real_log=True, holes=holes)
    res = checks.Result(rows, names={k: v.name for k, v in stations.items()})
    res.sim_hours = (end - start) / 3600
    ctx = checks.Context(sim, stations, {}, assets, spot_station)
    for check in REAL_CHECKS:
        check(res, ctx)
    name = "spoken item not played twice in a row"
    res.checks_run[name] = sum(1 for r in rows if r.kind in SPOKEN_TYPES)
    for sid, begun, title, seconds, length in doubled:
        tz = ZoneInfo(stations[sid].timezone or "America/New_York")
        res.findings.append(checks.Finding(
            name, sid, datetime.fromtimestamp(begun, UTC).astimezone(tz).strftime("%a %H:%M:%S"),
            f"{title!r} logged for {seconds:.1f} s, its length is {length:.1f} s: played twice back to back"))
    _paused_items_finish(res, rows, assets, blackouts, stations)
    name = "stretches with nothing logged (over 30 min)"
    res.checks_run[name] = sum(len(v) for v in holes.values())
    for sid, spans in holes.items():
        tz = ZoneInfo(stations[sid].timezone)
        for a, b in spans:
            covered = any(w["start"].timestamp() - 600 <= a and b <= w["end"].timestamp() + 600
                          and (not w["stations"] or sid in w["stations"]) for w in blackouts)
            if not covered:
                res.findings.append(checks.Finding(
                    name, sid, datetime.fromtimestamp(a, UTC).astimezone(tz).strftime("%a %b %d %H:%M"),
                    f"nothing logged for {(b - a) / 3600:.1f} h, until "
                    f"{datetime.fromtimestamp(b, UTC).astimezone(tz):%a %b %d %H:%M}"))
    return res


def main():
    since = _dt(sys.argv[2]) if len(sys.argv) > 2 else None
    res = check_export(json.loads(Path(sys.argv[1]).read_text()), since, sys.argv[3] if len(sys.argv) > 3 else None)
    print(res.report().replace("Simulated", "Real play log:", 1))


if __name__ == "__main__":
    main()
