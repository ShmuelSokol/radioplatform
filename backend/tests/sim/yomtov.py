"""Every Yom Tov of the coming year as broadcast days.

For each station: the Shabbos and Yom Tov windows the site's own generator makes for
the next 12 months, and for every one that holds a Yom Tov a simulated run from three
hours before candle lighting to three hours after the station comes back. Windows less
than 36 hours apart (Yom Tov, a weekday Friday, then Shabbos) are run as one stretch.

    uv run python -m tests.sim.yomtov --list
    uv run python -m tests.sim.yomtov --station main --index 0 --out /tmp/yomtov
"""
import argparse
import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.sim.checks import PLACES
from tests.sim.run import PICK, simulate, write_log

FIXTURE = Path(__file__).parent / "fixtures" / "stations-2026-11-19.json"
TZ = {"main": "America/New_York", "stories": "America/New_York", "jerusalem": "Asia/Jerusalem"}
LEAD = timedelta(hours=3)


def windows(station: str, first: date, last: date) -> list[dict]:
    from app.services.shabbos_service import (
        generate_shabbos_windows,
        generate_yom_tov_windows,
        merge_overlapping_windows,
    )

    tz = TZ[station]
    _, lat, lon, _ = PLACES[tz]
    made = merge_overlapping_windows(generate_shabbos_windows(lat, lon, tz, first, last, [PICK[station]])
                                     + generate_yom_tov_windows(lat, lon, tz, first, last, [PICK[station]]))
    out = [{"name": w["name"], "reason": w["reason"], "start": datetime.fromisoformat(w["start_datetime"]),
            "end": datetime.fromisoformat(w["end_datetime"])} for w in made]
    return sorted(out, key=lambda w: w["start"])


def stretches(station: str, first: date, last: date) -> list[list[dict]]:
    """Runs of windows to simulate together: each has at least one Yom Tov."""
    groups: list[list[dict]] = []
    for w in windows(station, first, last):
        if groups and w["start"] - groups[-1][-1]["end"] < timedelta(hours=36):
            groups[-1].append(w)
        else:
            groups.append([w])
    return [g for g in groups if any(w["reason"] != "Shabbos" for w in g)]


def measure(result, sid: str, group: list[dict], tz: ZoneInfo) -> list[dict]:
    rows = [r for r in result.rows if r.station == sid]
    out = []
    for w in group:
        ws, we = w["start"].timestamp(), w["end"].timestamp()
        before = next((r for r in reversed(rows) if r.start < ws - 1 and r.kind != "silence"), None)
        first_silence = next((r for r in rows if r.kind == "silence" and r.start >= ws - 2), None)
        inside = [r for r in rows if max(r.start, ws) + 1 < min(r.end, we)]
        other = [r for r in inside if r.kind != "silence"]
        back = next((r for r in rows if r.start >= we - 1 and r.kind != "silence"), None)
        gaps = [b.start - a.end for a, b in zip(inside, inside[1:]) if b.start - a.end > 1.0]
        out.append({
            "name": w["name"],
            "candle_lighting": w["start"].astimezone(tz).strftime("%a %b %d %Y %H:%M:%S"),
            "back": w["end"].astimezone(tz).strftime("%a %b %d %Y %H:%M:%S"),
            "hours": round((we - ws) / 3600, 1),
            "last_item_before": before.label() if before else None,
            "last_item_cut": bool(before.cut) if before else None,
            "last_item_end_vs_candles_s": round(before.end - ws, 1) if before else None,
            "silence_start_vs_candles_s": round(first_silence.start - ws, 1) if first_silence else None,
            "silence_items": len(inside) - len(other),
            "other_items_inside": [f"{r.local_start} {r.label()}" for r in other[:10]],
            "gaps_inside_s": [round(g, 1) for g in gaps[:10]],
            "first_item_back": back.label() if back else None,
            "first_item_back_vs_end_s": round(back.start - we, 1) if back else None,
        })
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--station", default="main")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--from", dest="first", default=None)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", default="/tmp/yomtov")
    p.add_argument("--list", action="store_true")
    p.add_argument("--quiet-pass", type=float, default=12.0)
    a = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    first = date.fromisoformat(a.first) if a.first else datetime.now(UTC).date()
    last = first + timedelta(days=a.days)
    if a.list:
        for st in PICK:
            tz = ZoneInfo(TZ[st])
            for i, g in enumerate(stretches(st, first, last)):
                hours = (g[-1]["end"] - g[0]["start"] + 2 * LEAD).total_seconds() / 3600
                print(f"{st} {i} {hours:.0f}h " + "; ".join(
                    f"{w['name']} {w['start'].astimezone(tz):%a %b %d %H:%M} - {w['end'].astimezone(tz):%a %b %d %H:%M}"
                    for w in g))
        return
    group = stretches(a.station, first, last)[a.index]
    tz = ZoneInfo(TZ[a.station])
    start = (group[0]["start"] - LEAD).astimezone(tz).replace(second=0, microsecond=0)
    hours = round((group[-1]["end"] + LEAD - start).total_seconds() / 3600, 2)
    out = Path(a.out)
    sim, result = asyncio.run(simulate(FIXTURE, start, hours, [a.station], out, quiet_pass=a.quiet_pass,
                                       regenerate_windows=True))
    tag = f"{a.station}-{a.index}"
    write_log(result, out / f"playlog-{tag}.csv")
    summary = {
        "station": a.station, "index": a.index, "start": start.isoformat(), "hours": hours,
        "wall_seconds": round(result.wall_seconds), "items": len(result.rows),
        "windows": measure(result, PICK[a.station], group, tz),
        "checks": {c: {"cases": n, "failing": len(result.failed(c))} for c, n in result.checks_run.items()},
        "findings": [{"check": f.check, "at": f.at, "text": f.text} for f in result.findings],
        "errors": [str(e) for e in result.errors[:20]],
    }
    (out / f"summary-{tag}.json").write_text(json.dumps(summary, indent=1))
    print(result.report())
    print(json.dumps(summary["windows"], indent=1))


if __name__ == "__main__":
    main()
