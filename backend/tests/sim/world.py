"""The simulator: virtual clock, patches, the scheduler pass and the fake engines."""
from __future__ import annotations

import asyncio
import heapq
import logging
import sys
import time as _real_time
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import time_machine

from tests.sim.engine import FakeEngine, item_from_db

logger = logging.getLogger("station_sim")

PASS_SECONDS = 3.0      # the scheduler's check_interval
QUIET_PASS_SECONDS = 3.0  # can be raised for long runs (see Simulator.quiet_pass)


# The queue page's estimated starts are read this often, for the hour after the read
SNAPSHOT_EVERY = 1800.0
SNAPSHOT_AHEAD = 3600.0


class _VirtualTime(types.ModuleType):
    """`time` as the app modules see it: monotonic() and time() follow the virtual clock;
    everything else is the real module."""

    def __init__(self, sim):
        super().__init__("time")
        self._sim = sim

    def monotonic(self):
        return self._sim.mono

    def monotonic_ns(self):
        return int(self._sim.mono * 1e9)

    def time(self):
        return self._sim.t

    def perf_counter(self):
        return _real_time.perf_counter()

    def __getattr__(self, name):
        return getattr(_real_time, name)


class Simulator:
    def __init__(self, db_path: Path, start: datetime, quiet_pass: float = QUIET_PASS_SECONDS):
        self.db_path = Path(db_path)
        self.start_dt = start.astimezone(UTC)
        self.t = self.start_dt.timestamp()
        self.mono = 1000.0
        self.quiet_pass = quiet_pass
        self.log: list = []
        self.engines: dict[str, FakeEngine] = {}
        self.hosts: list = []   # live show hosts (tests/sim/live.py), stepped on their own clock
        self._events: list = []
        self._seq = 0
        self._patched: list = []
        self.errors: list[str] = []
        self.passes = 0
        self.restarts: list = []
        # What the queue page showed (estimated_start), every SNAPSHOT_EVERY simulated
        # seconds: (when, station, {entry id: (estimated start, firm)}) for the hour
        # after; firm=False is shown "≈"
        self.snapshots: list = []
        self._next_snapshot = self.t

    # ── clock ──────────────────────────────────────────────────────────────────
    @property
    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    def _move(self, t: float):
        if t > self.t:
            self.mono += t - self.t
            self.t = t
            self._coords.move_to(datetime.fromtimestamp(t, UTC))

    def schedule(self, t: float, station: str, kind: str):
        self._seq += 1
        heapq.heappush(self._events, (t, self._seq, station, kind))

    # ── database ───────────────────────────────────────────────────────────────
    @asynccontextmanager
    async def session(self):
        async with self.Session() as s:
            try:
                yield s
            finally:
                try:
                    await s.rollback()
                except Exception:
                    pass

    async def item_for(self, entry_id: str, lane: str, uri: str):
        async with self.session() as db:
            return await item_from_db(db, entry_id, lane, uri)

    # ── set-up ─────────────────────────────────────────────────────────────────
    async def setup(self, seed):
        """`seed(db)` fills the empty database and returns the engine stations' ids."""
        import tests.conftest  # noqa: F401  (SQLite compilers for the Postgres types)
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy import event

        import app.models  # noqa: F401
        from app.db.base import Base

        self._traveller = time_machine.travel(self.start_dt, tick=False)
        self._coords = self._traveller.start()

        if self.db_path.exists():
            self.db_path.unlink()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}", echo=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragma(conn, _rec):
            c = conn.cursor()
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=OFF")
            c.execute("PRAGMA journal_mode=MEMORY")
            c.close()

        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as db:
            station_ids = await seed(db)
        self._patch(station_ids)
        await self._load_blackouts()
        from app.services import playout_engine
        playout_engine.reset_state()
        for sid in station_ids:
            eng = FakeEngine(self, sid)
            self.engines[str(sid)] = eng
        for eng in self.engines.values():
            await eng.start()
        return station_ids

    async def _load_blackouts(self):
        from sqlalchemy import select

        from app.models.holiday_window import HolidayWindow

        self.blackouts = []
        async with self.Session() as db:
            for h in (await db.execute(select(HolidayWindow))).scalars().all():
                if not h.is_blackout:
                    continue
                ids = set((h.affected_stations or {}).get("station_ids") or [])
                self.blackouts.append({
                    "start": h.start_datetime if h.start_datetime.tzinfo else h.start_datetime.replace(tzinfo=UTC),
                    "end": h.end_datetime if h.end_datetime.tzinfo else h.end_datetime.replace(tzinfo=UTC),
                    "stations": ids, "reason": h.reason or h.name,
                })

    def _set(self, obj, name, value):
        self._patched.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def _patch(self, station_ids):
        import app.db.engine as db_engine
        from app.api.v1 import queue as queue_module
        from app.config import settings
        from app.services import last_played, playout_engine, upcoming_analysis

        self._set(settings, "PLAYOUT_ENGINE_ENABLED", True)
        self._set(settings, "PLAYOUT_ENGINE_STATION_ID", ",".join(str(s) for s in station_ids))
        self._set(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
        self._set(settings, "PLAYOUT_ENGINE_TOKEN", "sim-token")
        self._set(settings, "PLAYOUT_STREAM_LIVE", True)
        # uploaded time clips and weather are looked up only with storage set up
        self._set(settings, "SUPABASE_URL", "https://storage.invalid")
        self._set(settings, "SUPABASE_SERVICE_KEY", "sim")
        self._set(upcoming_analysis, "ENABLED", False)
        self._set(last_played, "BACKGROUND_FULL_READ", False)

        async def command(path, payload=None, *, station_id, **_kw):
            eng = self.engines.get(str(station_id))
            return await eng.command(path, payload) if eng else False

        async def status(station_id):
            eng = self.engines.get(str(station_id))
            return eng.status() if eng else None

        self._set(playout_engine, "_command", command)
        self._set(playout_engine, "engine_status", status)

        async def no_broadcast(*_a, **_k):
            return None

        if hasattr(queue_module, "_broadcast_now_playing_entry"):
            self._set(queue_module, "_broadcast_now_playing_entry", no_broadcast)

        original_factory = db_engine.async_session_factory
        vtime = _VirtualTime(self)
        for name, mod in list(sys.modules.items()):
            if not name.startswith("app.") or mod is None:
                continue
            for attr, val in list(vars(mod).items()):
                if val is original_factory:
                    self._set(mod, attr, self.Session)
                elif val is _real_time:
                    self._set(mod, attr, vtime)

    async def teardown(self):
        for obj, name, val in reversed(self._patched):
            setattr(obj, name, val)
        self._patched.clear()
        from app.services import playout_engine
        playout_engine.reset_state()
        await self._settle()
        await self.engine.dispose()
        self._traveller.stop()

    # ── running ────────────────────────────────────────────────────────────────
    async def _settle(self):
        """Let background tasks the app started (refills, analysis) finish before the
        clock moves on, as they would in a few real milliseconds."""
        me = asyncio.current_task()
        for _ in range(3):
            keep = {h.task for h in self.hosts if h.task is not None}
            pending = [t for t in asyncio.all_tasks() if t is not me and not t.done() and t not in keep]
            if not pending:
                return
            await asyncio.wait(pending, timeout=0.5)

    async def _settle_live(self):
        """After a step of a live show: let what it started (a take-over, a time check
        marked as aired, the station handed back) finish before the clock moves."""
        me = asyncio.current_task()
        keep = {h.task for h in self.hosts if h.task is not None}
        for _ in range(50):
            pending = [t for t in asyncio.all_tasks() if t is not me and not t.done() and t not in keep]
            if not pending:
                return
            await asyncio.wait(pending, timeout=0.2)

    async def scheduler_pass(self):
        from app.services.scheduler_engine import SchedulerEngine

        if not hasattr(self, "_scheduler"):
            self._scheduler = SchedulerEngine()
        self.passes += 1
        async with self.Session() as db:
            try:
                await self._scheduler._check_all_stations(db)
                await self._scheduler._maybe_queue_weather_readouts(db)
            except Exception as e:  # the real loop logs and carries on
                self.errors.append(f"{self.now:%H:%M:%S} pass: {e!r}")
                logger.exception("scheduler pass")
            finally:
                try:
                    await db.rollback()
                except Exception:
                    pass

    async def restart_backend(self):
        """A deployment: the backend process is replaced. Everything it kept in memory about
        the engine is gone; the engine keeps playing and its next call teaches it again."""
        from app.services import playout_engine

        playout_engine.reset_state()
        self._scheduler = None
        del self._scheduler
        self.last_action = self.t
        self.restarts.append((self.t, "backend"))
        if getattr(self, "_live_installed", False):
            from tests.sim import live
            live.backend_restarted(self)

    async def restart_engine(self, sid: str):
        """The stream engine restarts (crash, setup.sh rerun): what was on air is cut, its
        lanes are empty, it says hello and asks for its next track."""
        eng = self.engines[sid]
        if eng.on_air is not None:
            eng._close(self.t, cut=True)
        eng.cued, eng.timed, eng.group, eng.given = None, [], [], []
        eng.timed_held.clear()
        eng.ask_next_at = None
        eng.live_on, eng.live_asked = False, None
        self.last_action = self.t
        self.restarts.append((self.t, f"engine {sid[:8]}"))
        await eng.start()

    def _pass_gap(self) -> float:
        """Every 3 s, as the real loop, near anything that matters: an item ending within
        20 s, a quarter-hour mark (time checks, breaks) within 60 s, a timed item handed
        over or an operator action. In between, every `quiet_pass` seconds: the engine's
        hand-over looks 45 s ahead, so nothing is handed over late."""
        if self.quiet_pass <= PASS_SECONDS:
            return PASS_SECONDS
        t = self.t
        to_mark = 900 - (t % 900)
        if to_mark < 60 or (t % 900) < 15:
            return PASS_SECONDS
        silent = 0
        for sid, e in self.engines.items():
            deep = self._deep_in_blackout(sid, t)
            # in the silence only the end of the file on air counts, not the engine's
            # own asking for a next song
            nt = (e.on_air.end if e.on_air is not None else None) if deep else e.next_event_time()
            if nt is not None and nt - t < 20:
                return PASS_SECONDS
            if deep:
                # Hours of one silence file after another: nothing to hand over or time
                silent += 1
                continue
            if e.timed or e.group or (e.on_air is not None and e.on_air.lane != "main"):
                return PASS_SECONDS
        if t - getattr(self, "last_action", -1e9) < 60 or self.hosts:
            return PASS_SECONDS
        quiet = self.quiet_pass * 3 if silent == len(self.engines) else self.quiet_pass
        return max(PASS_SECONDS, min(quiet, to_mark - 60))

    def _deep_in_blackout(self, sid: str, t: float, edge: float = 300.0) -> bool:
        """Is this station inside a Shabbos or Yom Tov window, more than five minutes from
        either end of it?"""
        for w in self.blackouts:
            if w["stations"] and sid not in w["stations"]:
                continue
            if w["start"].timestamp() + edge < t < w["end"].timestamp() - edge:
                return True
        return False

    async def _snapshot_estimates(self):
        """Read the queue as the control room does and keep each waiting entry's
        estimated start for the next SNAPSHOT_AHEAD seconds."""
        import uuid as _uuid

        from app.api.v1.queue import _get_queue_impl

        for sid in self.engines:
            try:
                async with self.session() as db:
                    body = await _get_queue_impl(_uuid.UUID(sid), 400, db)
            except Exception as e:  # a failed read is not what this looks at
                self.errors.append(f"queue read for estimates failed: {e}")
                continue
            est = {}
            for d in body.get("entries", []):
                if d.get("status") != "pending" or not d.get("estimated_start"):
                    continue
                ts = datetime.fromisoformat(d["estimated_start"]).timestamp()
                if self.t < ts <= self.t + SNAPSHOT_AHEAD:
                    est[d["id"]] = (ts, d.get("estimate_firm", True))
            self.snapshots.append((self.t, sid, est))

    async def run_until(self, end: datetime, on_step=None):
        end_t = end.astimezone(UTC).timestamp()
        next_pass = self.t
        while self.t < end_t:
            ev_t = self._events[0][0] if self._events else None
            eng_t = min((e.next_event_time() for e in self.engines.values()
                         if e.next_event_time() is not None), default=None)
            host_t = min((h.next for h in self.hosts if h.next is not None), default=None)
            nxt = min(x for x in (next_pass, ev_t, eng_t, host_t, end_t) if x is not None)
            self._move(max(nxt, self.t))
            while self._events and self._events[0][0] <= self.t + 1e-6:
                _, _, sid, kind = heapq.heappop(self._events)
                eng = self.engines.get(sid)
                if eng is not None:
                    await eng.on_event(kind)
            for host in list(self.hosts):
                if host.next is not None and host.next <= self.t + 1e-6:
                    await host.step()
            for eng in self.engines.values():
                await eng.tick()
            if self.t + 1e-6 >= next_pass:
                await self.scheduler_pass()
                await self._settle()
                next_pass = self.t + self._pass_gap()
            if self.t + 1e-6 >= self._next_snapshot:
                await self._snapshot_estimates()
                self._next_snapshot = self.t + SNAPSHOT_EVERY
            if on_step is not None:
                await on_step(self)
        on_air = {id(e.on_air) for e in self.engines.values() if e.on_air is not None}
        for seg in self.log:
            # (not what is still on air with no end of its own, a live show or the
            # emergency music: closing it here ended it at every scenario event)
            if seg.end is None and id(seg) not in on_air:
                seg.end = self.t
