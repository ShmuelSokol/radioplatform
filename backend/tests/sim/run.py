"""Run the station simulator from the command line.

    cd backend && uv run python -m tests.sim.run \
        --start 2026-11-19T19:30 --hours 3 --stations main

Start time is in New York time unless it carries an offset. Prints every check's result
and writes the play log (CSV) next to the database.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import random
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.sim import checks
from tests.sim.stations import DAF_BLOCK, JERUSALEM, MAIN, NAMES, STORIES, load_export, seed
from tests.sim.world import Simulator

NY = ZoneInfo("America/New_York")
PICK = {"main": MAIN, "jerusalem": JERUSALEM, "stories": STORIES}


async def simulate(export_path, start: datetime, hours: float, stations: list[str], out_dir: Path,
                   overrides: dict | None = None, quiet: bool = True, daf_every_day: bool = True,
                   quiet_pass: float = 3.0, restarts: int = 0, seed_value: int = 1):
    export = load_export(export_path)
    keep = {PICK[s] for s in stations}
    out_dir.mkdir(parents=True, exist_ok=True)
    sim = Simulator(out_dir / f"sim-{uuid.uuid4().hex[:6]}.db", start, quiet_pass=quiet_pass)

    async def do_seed(db):
        blocks = {}
        if daf_every_day:
            # Daf Yomi at 8 PM every day, as the PC job sets it up each day
            blocks = {b: {"is_active": True, "days_of_week": "0,1,2,3,4,5,6"} for b in DAF_BLOCK.values()}
        await seed(db, export, start, days=int(hours // 24) + 2, stations=keep, config_overrides=overrides,
                   block_overrides=blocks)
        return [uuid.UUID(s) for s in keep]

    wall = time.monotonic()
    await sim.setup(do_seed)
    try:
        if restarts:
            # backend and engine restarts at random moments, half of them aimed at the
            # worst ones: the seconds around a quarter-hour mark
            rnd = random.Random(seed_value)
            moments = []
            for _ in range(restarts):
                t = start + timedelta(seconds=rnd.uniform(600, hours * 3600 - 300))
                if rnd.random() < 0.5:
                    mark = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
                    t = mark + timedelta(seconds=rnd.choice([-50, -20, -8, -2, 1, 5, 20]))
                moments.append((t, rnd.choice(["backend", "engine"])))
            for t, what in sorted(moments):
                if t <= sim.now:
                    continue
                await sim.run_until(t)
                if what == "backend":
                    await sim.restart_backend()
                else:
                    for sid in list(sim.engines):
                        await sim.restart_engine(sid)
        await sim.run_until(start + timedelta(hours=hours))
        result = await checks.collect(sim)
        result.restarts = [(datetime.fromtimestamp(t, NY).strftime("%a %H:%M:%S"), w) for t, w in sim.restarts]
    finally:
        await sim.teardown()
        try:
            sim.db_path.unlink()
        except OSError:
            pass
    result.wall_seconds = time.monotonic() - wall
    result.sim_hours = hours
    return sim, result


def write_log(result, path: Path):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "start_local", "end_local", "seconds", "lane", "kind", "title", "cue_in", "cut",
                    "preempt_at", "timing_mode", "source", "entry"])
        for r in result.rows:
            w.writerow([NAMES.get(r.station, r.station), r.local_start, r.local_end, f"{r.seconds:.1f}", r.lane,
                        r.kind, r.title, f"{r.cue_in:.0f}", r.cut, r.preempt_at or "", r.timing_mode or "",
                        r.source or "", r.entry_id or ""])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--export", default=str(Path(__file__).parent / "fixtures" / "stations-2026-11-19.json"))
    p.add_argument("--start", required=True)
    p.add_argument("--hours", type=float, default=3)
    p.add_argument("--stations", default="main")
    p.add_argument("--out", default="/tmp/station-sim")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--restarts", type=int, default=0, help="backend/engine restarts at random and bad moments")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--quiet-pass", type=float, default=12.0,
                   help="seconds between scheduler passes when nothing is near (3 = always as live)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.ERROR)
    start = datetime.fromisoformat(a.start)
    if start.tzinfo is None:
        start = start.replace(tzinfo=NY)
    out = Path(a.out)
    sim, result = asyncio.run(simulate(a.export, start, a.hours, a.stations.split(","), out,
                                       quiet_pass=a.quiet_pass, restarts=a.restarts, seed_value=a.seed))
    log_path = out / f"playlog-{start:%Y%m%dT%H%M}-{a.stations.replace(',', '-')}.csv"
    write_log(result, log_path)
    print(result.report())
    if getattr(result, "restarts", None):
        print("restarts: " + ", ".join(f"{w} {t}" for t, w in result.restarts))
    print(f"play log: {log_path}")


if __name__ == "__main__":
    main()
