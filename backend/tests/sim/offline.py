"""The local FM box (BMG room) with its internet line cut for hours, on the virtual clock.

The real relay (infra/local/kbr_relay.py) stands between the simulated engine and the
backend, as on the box: with the line up it passes /next, /started and /hello on and
fetches the plan every minute; with the line down it answers from the plan on its disk,
hands hard items to the engine itself and keeps the start reports until the line is back.

    cd backend && uv run python -m tests.sim.offline --start 2026-11-19T17:30 \
        --cut 2026-11-19T18:00 --back 2026-11-20T10:00 --end 2026-11-20T12:00

While the line is down, changes are made on the site (`--changes`): an operator queues a
song to play next and removes 20 coming songs. The report says what aired on the box,
whether time checks and Shabbos silence held, and what the site knows afterwards.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import tempfile
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from tests.sim import checks
from tests.sim.stations import DAF_BLOCK, MAIN, load_export, seed
from tests.sim.world import Simulator

NY = ZoneInfo("America/New_York")
RELAY_PATH = Path(__file__).resolve().parents[3] / "infra" / "local" / "kbr_relay.py"


def _relay_module():
    spec = importlib.util.spec_from_file_location("kbr_relay", RELAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Box:
    """The relay on the box, its line to the backend and its engine."""

    def __init__(self, sim: Simulator, station_id, data_dir: Path):
        self.sim, self.station_id = sim, station_id
        self.line_up = True
        self.loop = asyncio.get_running_loop()
        self.kr = _relay_module()
        box = self

        class SimRelay(self.kr.Relay):
            def _call(self, method, path, body=None, timeout=5.0):
                if not box.line_up:
                    self._set_online(False)
                    raise box.kr.Offline("line cut")
                fut = asyncio.run_coroutine_threadsafe(box.backend(method, path, body), box.loop)
                result = fut.result()
                self._set_online(True)
                return result

            def have(self, url):  # the whole library is on the box's disk
                return True

            def localize(self, uri):
                return uri

            def silence_file(self):
                return Path("/box/silence-60s.wav")

            def _post_engine(self, path, payload):
                eng = box.sim.engines[str(box.station_id)]
                return asyncio.run_coroutine_threadsafe(eng.command(path, payload), box.loop).result()

        self.relay = SimRelay("http://backend.invalid/api/v1", str(station_id), "sim-token", data_dir,
                              clock=lambda: sim.t, mirror_library=False)
        self.last_sync = -1e9
        self.kept_max = 0

    # the backend's playout routes, as app/api/v1/playout.py calls them
    async def backend(self, method: str, path: str, body: bytes | None):
        from app.services import playout_engine, playout_plan

        route, _, query = path.partition("?")
        data = json.loads(body) if body else {}
        async with self.sim.session() as db:
            if route == "/next":
                q = urllib.parse.parse_qs(query)
                given = [g for g in (q.get("given", [""])[0]).split(",") if g]
                got = await playout_engine.handle_next(db, self.station_id, given, proto=5, engine_live_on=False)
                return (204, b"") if got is None else (200, json.dumps(got).encode())
            if route == "/started":
                got = await playout_engine.handle_started(
                    db, self.station_id, data.get("entry_id"), data.get("lane"), at=data.get("at"),
                    kept=bool(data.get("kept")), quiet=bool(data.get("kept") and data.get("more")))
                return 200, json.dumps(got).encode()
            if route == "/hello":
                await playout_engine.handle_hello(self.station_id, data.get("proto"), "sim-box")
                return 200, b'{"ok":true}'
            if route == "/plan":
                return 200, json.dumps(await playout_plan.build_plan(db, self.station_id)).encode()
        return 404, b"{}"

    # what the engine calls (tests/sim/engine.py `backend`)
    async def next(self, given):
        query = urllib.parse.urlencode({"given": ",".join(given), "proto": 5})
        status, data = await asyncio.to_thread(self.relay.next, query)
        return json.loads(data) if status == 200 else None

    async def started(self, entry_id, lane, at):
        body = json.dumps({"entry_id": entry_id, "lane": lane, "at": at}).encode()
        await asyncio.to_thread(self.relay.started, body)

    async def step(self, _sim):
        """The relay's background loops: plan every minute (and just before a hard item),
        catch-up when the line is there, timed hand-over."""
        r = self.relay
        t = self.sim.t
        due = t - self.last_sync >= 60.0 or (r.hard_item_soon() and t - r.last_plan_at > 20.0)
        if due:
            self.last_sync = t
            if await asyncio.to_thread(r.sync_plan):
                await asyncio.to_thread(r.catch_up)
        await asyncio.to_thread(r.hand_over_timed)
        try:
            self.kept_max = max(self.kept_max, sum(1 for _ in r.spool_path.open()))
        except OSError:
            pass


async def run(export_path, start, cut, back, end, changes=True, out=None):
    export = load_export(export_path)
    tmp = Path(tempfile.mkdtemp(prefix="station-sim-box-"))
    sim = Simulator(tmp / "sim.db", start, quiet_pass=12.0)
    sid = uuid.UUID(MAIN)

    async def do_seed(db):
        await seed(db, export, start, days=3, stations={MAIN},
                   block_overrides={b: {"is_active": True, "days_of_week": "0,1,2,3,4,5,6"} for b in DAF_BLOCK.values()})
        return [sid]

    wall = time.monotonic()
    await sim.setup(do_seed)
    box = Box(sim, sid, tmp / "box")
    eng = sim.engines[MAIN]
    eng.backend = box
    notes: list[str] = []
    try:
        from app.services import playout_engine

        # the backend reaches a box only through its relay: while the line is down, not at all
        inner = playout_engine._command

        async def command(path, payload=None, *, station_id, **kw):
            if not box.line_up:
                return False
            return await inner(path, payload, station_id=station_id, **kw)

        playout_engine._command = command
        await sim.run_until(cut, on_step=box.step)
        box.line_up = False
        plan_at_cut = len(box.relay.plan.get("entries", []))
        notes.append(f"line cut {cut.astimezone(NY):%a %H:%M}: plan on the box holds {plan_at_cut} items")
        made = {}
        if changes:
            mid = cut + (back - cut) / 4
            await sim.run_until(mid, on_step=box.step)
            made = await _site_changes(sim, sid)
            notes.append(f"{mid.astimezone(NY):%a %H:%M} on the site: {made['note']}")
        await sim.run_until(back, on_step=box.step)
        kept = box.kept_max
        box.line_up = True
        notes.append(f"line back {back.astimezone(NY):%a %H:%M}: {kept} start reports kept on the box")
        await sim.run_until(end, on_step=box.step)
        playout_engine._command = inner
        result = await checks.collect(sim)
        report = await _after(sim, sid, result, cut, back, made, notes)
    finally:
        await sim.teardown()
    result.wall_seconds = time.monotonic() - wall
    result.sim_hours = (end - start).total_seconds() / 3600
    text = result.report() + "\n\nOffline run:\n" + "\n".join("  " + n for n in report)
    if out:
        Path(out).write_text(text)
    return result, report, text


async def _site_changes(sim, sid) -> dict:
    """What staff do on the site while the box cannot hear it."""
    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry

    async with sim.Session() as db:
        pending = (await db.execute(
            select(QueueEntry).where(QueueEntry.station_id == sid, QueueEntry.status == "pending",
                                     QueueEntry.preempt_at.is_(None), QueueEntry.source == "auto")
            .order_by(QueueEntry.position))).scalars().all()
        removed = [str(e.id) for e in pending[30:50]]
        for e in pending[30:50]:
            await db.delete(e)
        song = (await db.execute(select(Asset).where(Asset.asset_type == "music").limit(1))).scalar_one()
        head = pending[0].position if pending else 1
        wanted = QueueEntry(id=uuid.uuid4(), station_id=sid, asset_id=song.id, position=head,
                            status="pending", source="manual")
        db.add(wanted)
        await db.commit()
        return {"removed": removed, "play_next": str(wanted.id), "play_next_title": song.title,
                "note": f"20 coming songs removed; '{song.title}' queued to play next"}


async def _after(sim, sid, result, cut, back, made, notes) -> list[str]:
    from app.models.play_log import PlayLog
    from app.models.queue_entry import QueueEntry

    c, b = cut.timestamp(), back.timestamp()
    off = [r for r in result.rows if c <= r.start < b]
    with_entry = [r for r in off if r.entry_id]
    async with sim.Session() as db:
        logs = (await db.execute(select(PlayLog).where(PlayLog.station_id == sid))).scalars().all()
        entries = {str(e.id): e for e in (await db.execute(select(QueueEntry).where(QueueEntry.station_id == sid))).scalars().all()}
    log_starts = {}
    for p in logs:
        s = p.start_utc if p.start_utc.tzinfo else p.start_utc.replace(tzinfo=UTC)
        log_starts.setdefault(str(p.asset_id), []).append(s.timestamp())
    missing, shifted = [], []
    for r in with_entry:
        times = log_starts.get(r.asset_id or "", [])
        if not times:
            missing.append(r)
            continue
        nearest = min(times, key=lambda t: abs(t - r.start))
        if abs(nearest - r.start) > 5:
            shifted.append((r, nearest - r.start))
    notes.append(f"offline {((b - c) / 3600):.1f} h: {len(off)} items aired on the box "
                 f"({sum(1 for r in off if r.lane == 'emergency')} emergency-music stretches, "
                 f"{sum(1 for r in off if not r.entry_id and r.kind == 'silence')} Shabbos silence files)")
    hard = [r for r in off if r.timing_mode == "hard" and r.preempt_at]
    late = [r for r in hard if abs(r.start - r.preempt_at.timestamp()) > 1 and r is min(
        (x for x in hard if x.preempt_at == r.preempt_at), key=lambda x: x.start)]
    notes.append(f"hard timed groups offline: {len({r.preempt_at for r in hard})}, off their second: {len(late)}")
    notes.append(f"site play log after the line came back: {len(with_entry) - len(missing) - len(shifted)} of "
                 f"{len(with_entry)} offline plays logged at their real time, {len(shifted)} at a wrong time, "
                 f"{len(missing)} not logged")
    for r in missing[:6]:
        notes.append(f"    not logged: {r.label()}")
    for r, d in shifted[:6]:
        notes.append(f"    logged {d:+.0f} s off: {r.label()}")
    sponsor_off = [r for r in with_entry if r.is_sponsorship]
    notes.append(f"sponsorship plays aired offline: {len(sponsor_off)}, of them not in the site's log: "
                 f"{sum(1 for r in sponsor_off if r in missing)}")
    if made:
        removed_aired = [r for r in result.rows if r.entry_id in set(made["removed"])]
        notes.append(f"songs removed on the site that the box still aired: {len(removed_aired)} of {len(made['removed'])}")
        wanted = [r for r in result.rows if r.entry_id == made["play_next"]]
        notes.append("the song queued 'play next' on the site aired "
                     + (f"at {wanted[0].local_start} ({(wanted[0].start - b) / 60:+.0f} min from the line's return)"
                        if wanted else "never (by the end of the run)"))
    after = [r for r in result.rows if r.start >= b]
    seen = {r.asset_id for r in off if r.asset_id and r.kind == "music"}
    again = [r for r in after[:40] if r.kind == "music" and r.asset_id in seen]
    notes.append(f"first 40 items after the return: {len(again)} songs that already aired while offline")
    stuck = [e for e in entries.values() if e.status == "playing"]
    notes.append(f"queue rows marked on air at the end: {len(stuck)}")
    return notes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--export", default=str(Path(__file__).parent / "fixtures" / "stations-2026-11-19.json"))
    for name in ("start", "cut", "back", "end"):
        p.add_argument(f"--{name}", required=True)
    p.add_argument("--no-changes", action="store_true")
    p.add_argument("--out")
    a = p.parse_args()
    logging.basicConfig(level=logging.ERROR)
    when = [datetime.fromisoformat(getattr(a, n)).replace(tzinfo=NY) for n in ("start", "cut", "back", "end")]
    _, _, text = asyncio.run(run(a.export, *when, changes=not a.no_changes, out=a.out))
    print(text)


if __name__ == "__main__":
    main()
