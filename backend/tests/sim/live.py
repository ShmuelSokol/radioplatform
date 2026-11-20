"""Live shows in the station simulator: a host, the broadcast delay, Dump, the end.

The real code runs: the host's audio socket handler (`websocket_live_audio`, with a
stand-in socket), the station relay, the delay line (`LiveAir`), the timed inserts,
`live_show_service` and the scheduler's live-show checks. What is replaced:

- the delay's pump: `LiveAir.step` is called on the virtual clock, 20 times a virtual
  second, right after the host's frame for those 50 ms went in (LiveAir.start is a no-op);
- ffmpeg: a time check aired over the delay is a clip of its asset's length;
- the once-a-second look for timed items while the delay fills runs on the virtual clock.

The host "speaks" numbers: every 50 ms frame holds one value that says which frame it
is, with half a second of silence every five seconds (a host breathes). What listeners
get is read back from the delay's output, so a check can say exactly which of the
host's words aired, which a Dump cut, and how late each was heard.

Not simulated: the stream server's own lag (2.5 s on the real stream; the scenarios set
`live_stream_lag_seconds` 0), real audio, the mp3 feed, web sockets of listeners.
"""
from __future__ import annotations

import asyncio
import types
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

TICK = 0.05
BASE = 10000      # a host frame's value: BASE + (frame number % WRAP)
WRAP = 10000
PAUSE_EVERY, PAUSE_FRAMES = 100, 10   # of every 100 frames the last 10 are silent
# A caller's frame: one value no host frame has (frame 95 is one the host is silent in),
# so host + caller, the caller alone and the host alone all read apart
CALLER = BASE + 95


class FakeSocket:
    """What the audio socket handler needs of a WebSocket."""

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.idle = asyncio.Event()
        self.closed: int | None = None

    async def receive_bytes(self):
        from starlette.websockets import WebSocketDisconnect

        if self.q.empty():
            self.idle.set()
        item = await self.q.get()
        self.idle.clear()
        if item is None:
            self.idle.set()
            raise WebSocketDisconnect(1000)
        return item

    async def close(self, code: int = 1000):
        if self.closed is None:
            self.closed = code
            self.q.put_nowait(None)

    async def accept(self): ...
    async def send_bytes(self, _data): ...
    async def send_text(self, _data): ...
    async def send_json(self, _data): ...


@dataclass
class Heard:
    t: float            # when listeners got this 50 ms
    kind: str           # host, quiet, insert:<n>, filler
    frame: float = -1   # which of the host's frames (host only)
    caller: bool = False  # a caller's sound is in these 50 ms


def install(sim) -> None:
    """Patches for live shows; undone by the simulator's teardown."""
    if getattr(sim, "_live_installed", False):
        return
    sim._live_installed = True
    from app.api.v1 import live_shows_ws
    from app.services import live_inserts, live_show_service, scheduler_engine
    from app.services.live_air import RATE, LiveAir

    sim.live_clips = {}   # url -> (seconds, code)
    sim.live_codes = {}   # code -> title
    sim.live_holds = []   # (station, air, started): the look for timed items while the delay fills
    sim.live_log = []     # every host of the run, for the checks

    async def auth(_ws, _show_id):
        return types.SimpleNamespace(id=None, email="host@sim")

    sim._set(live_shows_ws, "_authenticate_ws", auth)
    sim._set(LiveAir, "start", lambda self: None)

    async def decode(url):
        seconds, code = sim.live_clips.get(url, (6.0, 9000))
        return np.full(int(seconds * RATE), -(1000 + code), dtype=np.int16)

    sim._set(live_inserts, "decode_clip", decode)
    original_load = live_inserts._load

    async def load(air, entry_id, station_id, asset_id, title, kind, url, at):
        from app.models.asset import Asset

        async with sim.session() as db:
            asset = await db.get(Asset, asset_id)
            seconds = float(asset.duration or 5.0) if asset else 5.0
        code = len(sim.live_clips) + 1
        sim.live_clips.setdefault(url, (seconds, code))
        sim.live_codes[sim.live_clips[url][1]] = (str(entry_id), title)
        await original_load(air, entry_id, station_id, asset_id, title, kind, url, at)

    sim._set(live_inserts, "_load", load)

    async def hold(station_id, air):
        sim.live_holds.append((station_id, air, air.clock()))

    sim._set(live_show_service, "hold_for_timed_items", hold)
    sim._set(live_inserts, "_preparing", set())

    def ready(sid):
        air = live_shows_ws.live_relay.airs.get(sid)
        return air is not None and air.listening and not air.closed

    sim.live_ready = ready
    # The relay and the scheduler the app keeps for the whole process
    relay = live_shows_ws.live_relay
    for name in ("on_air", "hosts", "airs", "on_first_air", "callers"):
        getattr(relay, name).clear()
    relay.quieted_early.clear()
    sim._set(scheduler_engine, "_scheduler_instance", None)
    _own_scheduler(sim)


def _own_scheduler(sim) -> None:
    """The simulator's scheduler is the process's scheduler (the host-gone rule asks for it)."""
    from app.services import scheduler_engine
    from app.services.scheduler_engine import SchedulerEngine

    if not hasattr(sim, "_scheduler"):
        sim._scheduler = SchedulerEngine()
    sim._scheduler.running = True
    scheduler_engine._scheduler_instance = sim._scheduler


async def create_show(sim, station_id: str, start: datetime, minutes: float, title: str = "Sim show") -> str:
    from app.services import live_show_service

    install(sim)
    async with sim.Session() as db:
        from app.schemas.live_show import LiveShowCreate

        show = await live_show_service.create_show(db, LiveShowCreate(
            station_id=str(station_id), title=title, scheduled_start=start.astimezone(UTC),
            scheduled_end=start.astimezone(UTC) + timedelta(minutes=minutes)))
        await db.commit()
        return str(show.id)


@dataclass
class SimHost:
    sim: object
    station_id: str
    show_id: str
    next: float | None = None
    ws: FakeSocket | None = None
    task: asyncio.Task | None = None
    frame: int = 0                 # frames spoken so far
    muted: bool = False            # connected, sending nothing (a frozen page)
    heard: list = field(default_factory=list)
    said: dict = field(default_factory=dict)       # frame -> when it was spoken
    dumps: list = field(default_factory=list)      # (when, first frame cut, last frame cut, seconds)
    callers: list = field(default_factory=list)    # SimCaller: web callers of this show
    marks: dict = field(default_factory=dict)      # first_heard, ended, released ...
    _air: object = None
    _insert: tuple | None = None
    reconnect_at: float | None = None   # the page finds its socket gone and opens it again
    _look_at: float = 0.0

    @property
    def sid(self) -> str:
        return str(self.station_id)

    @property
    def air(self):
        from app.api.v1.live_shows_ws import live_relay
        return live_relay.airs.get(self.sid)

    async def _settle(self):
        """Let the socket handler take what was sent (and finish its database work)."""
        for _ in range(20000):
            if self.task is None or self.task.done() or (self.ws.q.empty() and self.ws.idle.is_set()):
                return
            await asyncio.sleep(0)
        raise RuntimeError("the host's audio socket handler did not settle")

    async def connect(self):
        """Turn on microphone + GO LIVE: the audio socket opens and sound flows."""
        from app.api.v1 import live_shows_ws

        install(self.sim)
        _own_scheduler(self.sim)
        self.ws = FakeSocket()
        self.task = asyncio.ensure_future(live_shows_ws.websocket_live_audio(self.ws, self.show_id))
        for _ in range(200000):
            if self.task.done() or self.ws.idle.is_set():
                break
            await asyncio.sleep(0)
        await self.sim._settle_live()
        if self not in self.sim.hosts:
            self.sim.hosts.append(self)
        if self not in self.sim.live_log:
            self.sim.live_log.append(self)
        self.next = self.sim.t + TICK
        self.marks.setdefault("connected", self.sim.t)
        self.sim.last_action = self.sim.t
        return self.ws.closed

    def _watch(self, air):
        if air is None or air is self._air:
            return
        self._air = air
        remember = air._remember

        def record(out, _remember=remember):
            self._record(out)
            _remember(out)

        air._remember = record

    def _record(self, out):
        if out is None or len(out) == 0:
            return
        t = self.sim.t
        sample = np.asarray(out)[:: max(1, len(out) // 12)].astype(np.int32)
        host = sample[sample >= BASE // 2]
        if len(host) * 2 >= len(sample):
            value = float(np.median(host))
            if float(host.max()) - float(host.min()) > 200:
                # the delay building back blends the frames either side of a pause of the
                # host while a caller talks: show sound, but no one frame's number
                self.heard.append(Heard(t, "blend", caller=True))
            elif value == CALLER:
                self.heard.append(Heard(t, "caller", caller=True))   # the caller, the host silent
            elif value >= BASE + WRAP:
                self.heard.append(Heard(t, "host", value - CALLER - BASE, caller=True))
            else:
                self.heard.append(Heard(t, "host", value - BASE))
            self.marks.setdefault("first_heard", t)
        elif (sample < -500).sum() * 2 >= len(sample):
            code = int(round(-float(np.median(sample[sample < -500])))) - 1000
            kind = "filler" if code >= 9000 else f"insert:{code}"
            if kind != "filler" and (self._insert is None or self._insert[0] != kind):
                # A time check over the delay is an item on air: a row of the log
                from tests.sim.engine import Segment

                entry_id, title = self.sim.live_codes.get(code, (None, "insert"))
                seg = Segment(self.sid, entry_id, "live", t - TICK, t, title, "insert")
                self.sim.log.append(seg)
                self._insert = (kind, seg)
            elif kind != "filler":
                self._insert[1].end = t
            self.heard.append(Heard(t, kind))
            self.marks.setdefault("first_heard", t)
        else:
            self.heard.append(Heard(t, "quiet"))

    async def step(self):
        from app.services import live_show_service
        from app.services.live_air import RATE

        n = int(RATE * TICK)
        if self.ws is None and self.reconnect_at is not None and self.sim.t >= self.reconnect_at:
            self.reconnect_at = None
            self.marks.setdefault("reconnected", self.sim.t)
            await self.connect()
        if self.ws is not None and self.ws.closed is None and not self.muted and not self.task.done():
            if self.frame % PAUSE_EVERY >= PAUSE_EVERY - PAUSE_FRAMES:
                pcm = np.zeros(n, dtype="<i2")
            else:
                pcm = np.full(n, BASE + self.frame % WRAP, dtype="<i2")
            for caller in self.callers:
                caller.speak(n)
            self.said[self.frame] = self.sim.t
            self.frame += 1
            self.ws.idle.clear()
            self.ws.q.put_nowait(pcm.tobytes())
            await self._settle()
        air = self.air
        self._watch(air)
        if air is not None and not air.closed:
            if not await air.step(n):
                await air._close()
            for sid, held, started in list(self.sim.live_holds):
                if held.listening or held.closed or held.finishing:
                    self.sim.live_holds.remove((sid, held, started))
                elif self.sim.t - self._look_at >= 1.0:
                    self._look_at = self.sim.t
                    await live_show_service.look_for_timed_items(sid, held, started)
        await self.sim._settle_live()
        self.next = self.sim.t + TICK
        gone = self.ws is None or self.ws.closed is not None or self.task.done()
        if gone and self.reconnect_at is None and (air is None or air.closed):
            self.next = None
            if self in self.sim.hosts:
                self.sim.hosts.remove(self)

    # ── what a person at the Host Console does ─────────────────────────────────
    async def dump(self) -> float:
        """The Dump button (POST /live-shows/{id}/dump without its log row)."""
        air = self.air
        if air is None or air.buffered < 0.05:
            return 0.0
        cut = air.dump()
        frames = int(round(cut / TICK))
        self.dumps.append((self.sim.t, self.frame - frames, self.frame - 1, cut))
        self.sim.last_action = self.sim.t
        return cut

    async def end_show(self):
        """End Show (POST /live-shows/{id}/end), then the host's page stops sending."""
        from app.services import live_show_service

        self.marks["ended"] = self.sim.t
        self.marks["last_frame"] = self.frame - 1
        async with self.sim.Session() as db:
            await live_show_service.end_show(db, uuid.UUID(self.show_id))
            await db.commit()
        await self.leave()

    async def leave(self):
        """The host's page closes (or its connection drops): the socket ends."""
        self.marks.setdefault("left", self.sim.t)
        if self.ws is not None and self.ws.closed is None:
            await self.ws.close(1001)
            for _ in range(200000):
                if self.task.done():
                    break
                await asyncio.sleep(0)
        await self.sim._settle_live()
        self.sim.last_action = self.sim.t


class SimCaller:
    """A web caller: their page's microphone runs from the moment they call, the staff
    approve them, put them on air, hold them and end the call (the services the
    screener's buttons call). `air` keeps (on, off) for every stretch on air."""

    def __init__(self, host: SimHost, name: str = "Caller"):
        self.host, self.name = host, name
        self.call_id: str | None = None
        self.line = None
        self.air: list[list[float]] = []

    def speak(self, n: int) -> None:
        if self.line is not None:
            self.line.add(np.full(n, CALLER, dtype="<i2").tobytes())

    async def call(self):
        from app.api.v1.live_shows_ws import CallerLine, live_relay
        from app.models.call_in_request import CallInRequest, CallStatus

        async with self.host.sim.Session() as db:
            row = CallInRequest(live_show_id=uuid.UUID(self.host.show_id), caller_phone="web",
                                caller_name=self.name, status=CallStatus.WAITING)
            db.add(row)
            await db.commit()
            self.call_id = str(row.id)
        self.line = CallerLine(FakeSocket(), self.call_id, self.host.show_id, self.host.sid)
        live_relay.callers[self.call_id] = self.line
        self.host.callers.append(self)

    async def _move(self, what: str, status: str):
        from app.api.v1.live_shows_ws import live_relay
        from app.services import live_show_service as svc

        async with self.host.sim.Session() as db:
            if what == "approve":
                await svc.approve_call(db, self.call_id)
            elif what == "on_air":
                await svc.put_caller_on_air(db, uuid.UUID(self.host.show_id), self.call_id)
            elif what == "hold":
                await svc.hold_caller(db, self.call_id)
            else:
                await svc.end_call(db, self.call_id)
            await db.commit()
        await live_relay.caller_status(self.call_id, status)
        self.host.sim.last_action = self.host.sim.t

    async def approve(self):
        await self._move("approve", "approved")

    async def on_air(self):
        await self._move("on_air", "on_air")
        self.air.append([self.host.sim.t, float("inf")])

    async def hold(self):
        await self._move("hold", "approved")
        self.air[-1][1] = self.host.sim.t

    async def hang_up(self):
        await self._move("end", "completed")
        if self.air and self.air[-1][1] == float("inf"):
            self.air[-1][1] = self.host.sim.t
        self.line = None


def backend_restarted(sim, reconnect_after: float = 3.0) -> None:
    """A deployment in mid show: everything the backend held in memory is gone (the
    delay with the words waiting in it, who is on air, the host's socket). The host's
    page notices its socket closed and opens it again by itself."""
    from app.api.v1 import live_shows_ws
    from app.services import live_inserts

    relay = live_shows_ws.live_relay
    for name in ("on_air", "hosts", "airs", "on_first_air", "callers"):
        getattr(relay, name).clear()
    relay.quieted_early.clear()
    live_inserts._preparing.clear()
    sim.live_holds.clear()
    for host in list(sim.hosts):
        if host.task is not None and not host.task.done():
            host.task.cancel()
        host.ws, host._air, host._insert = None, None, None
        host.reconnect_at = sim.t + reconnect_after
        host.marks.setdefault("backend_restart", sim.t)
    _own_scheduler(sim)


async def go_live(sim, station_id: str, show_id: str) -> SimHost:
    host = SimHost(sim, str(station_id), show_id)
    await host.connect()
    return host


# ── checks ────────────────────────────────────────────────────────────────────

LAG_OVER = 12.0    # a word may air this much later than the delay (a time check aired over it)
QUIET_MAX = 1.6    # the host's half-second breath, played at half speed while the delay builds


def absolute(heard: list) -> list:
    """(time, frame number since the host began) for every 50 ms of the host that aired."""
    out, prev = [], 0.0
    for h in heard:
        if h.kind != "host":
            continue
        frame = h.frame + WRAP * round((prev - h.frame) / WRAP)
        out.append((h.t, frame))
        prev = frame
    return out


def checks(res, ctx) -> None:
    from tests.sim.checks import _add, _count

    names = {
        "first": "live show: music stops when the host is first heard, not before, not after",
        "quiet": "live show: no silence while the show has the air",
        "dump": "live show: a Dump cuts those words and only those",
        "lag": "live show: listeners hear the host through the delay, never early",
        "end": "live show: the last words air, then the station carries on at once",
        "after": "live show: nothing of the host after the show",
    }
    hosts = getattr(ctx.sim, "live_log", [])
    if not hosts:
        return
    for n in names.values():
        res.checks_run.setdefault(n, 0)

    def clock(sid, t):
        from datetime import UTC as _UTC
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(ctx.stations[sid].timezone or "America/New_York")
        return datetime.fromtimestamp(t, _UTC).astimezone(tz).strftime("%a %H:%M:%S.%f")[:-4]

    for host in hosts:
        sid = host.sid
        shows = ctx.shows(sid)
        heard = host.heard
        first = host.marks.get("first_heard")
        if first is None:
            continue
        # the stream became the show's as the first words came out of the delay
        _count(res, names["first"])
        mine = min(shows, key=lambda w: abs(w[0] - first), default=None)
        if mine is None or abs(mine[0] - first) > 1.5:
            _add(res, names["first"], sid, clock(sid, first),
                 "the host was first heard here; the stream went to the show "
                 + (f"{mine[0] - first:+.1f} s from it" if mine else "never"))
        # silence inside the show
        run_start = None
        for h in heard + [Heard(heard[-1].t + TICK, "end")]:
            if h.kind == "quiet":
                run_start = h.t if run_start is None else run_start
                continue
            if run_start is not None:
                _count(res, names["quiet"])
                if h.t - run_start > QUIET_MAX and ctx.in_show(sid, run_start):
                    _add(res, names["quiet"], sid, clock(sid, run_start), f"{h.t - run_start:.1f} s of silence in the show")
            run_start = None
        gaps = [(a.t, b.t) for a, b in zip(heard, heard[1:]) if b.t - a.t > QUIET_MAX and ctx.in_show(sid, a.t)
                and ctx.in_show(sid, b.t)]
        for a, b in gaps:
            _count(res, names["quiet"])
            _add(res, names["quiet"], sid, clock(sid, a), f"nothing came out of the delay for {b - a:.1f} s")
        # which of the host's words aired
        aired = absolute(heard)
        frames = {int(round(f)) for _t, f in aired}
        for when, f0, f1, cut in host.dumps:
            _count(res, names["dump"])
            leaked = sorted(f for f in frames if f0 + 2 <= f <= f1 - 2)
            if leaked:
                _add(res, names["dump"], sid, clock(sid, when),
                     f"{len(leaked) * TICK:.1f} s of the {cut:.1f} s that were dumped aired anyway")
            before = [f for f in range(f0 - 40, f0 - 4) if f % PAUSE_EVERY < PAUSE_EVERY - PAUSE_FRAMES and f >= 0]
            lost = [f for f in before if not any(abs(f - g) <= 2 for g in frames)]
            if before and len(lost) > len(before) // 2:
                _add(res, names["dump"], sid, clock(sid, when), "words said before the dumped part did not air either")
        # each word airs after it was said, by about the delay
        worst_early, worst_late = None, None
        target = host.marks.get("delay", 15.0)
        # A show that waited for a time check to end before it was heard starts with a
        # longer delay (the host's words waited meanwhile) and shrinks it as it goes
        held = max(0.0, first - host.marks.get("connected", first) - target)
        for t, f in aired:
            said = host.said.get(int(round(f)))
            if said is None:
                continue
            lag = t - said
            if lag < -0.2 and (worst_early is None or lag < worst_early[0]):
                worst_early = (lag, t)
            if lag > target + held + LAG_OVER and (worst_late is None or lag > worst_late[0]):
                worst_late = (lag, t)
        _count(res, names["lag"])
        if worst_early:
            _add(res, names["lag"], sid, clock(sid, worst_early[1]), f"a word aired {-worst_early[0]:.1f} s before it was said")
        if worst_late:
            _add(res, names["lag"], sid, clock(sid, worst_late[1]),
                 f"a word aired {worst_late[0]:.1f} s after it was said (delay {target:.0f} s)")
        # the end
        ended = host.marks.get("ended")
        if ended is not None and aired:
            _count(res, names["end"])
            last_t, last_f = aired[-1]
            want = host.marks.get("last_frame", 0)
            # (the last frames may be a breath)
            if host.marks.get("drain", True) and want - last_f > PAUSE_FRAMES + 4:
                _add(res, names["end"], sid, clock(sid, ended),
                     f"the last {(want - last_f) * TICK:.1f} s the host said before End never aired")
            over = next((b for a, b in shows if a <= last_t <= b + 1), None)
            if over is None:
                _add(res, names["end"], sid, clock(sid, last_t), "the show's last words aired outside the show's time on the stream")
            else:
                if over - last_t > 2.0:
                    _add(res, names["end"], sid, clock(sid, last_t),
                         f"{over - last_t:.1f} s of silence between the host's last word and the station carrying on")
                nxt = min((r.start for r in res.rows if r.station == sid and r.entry_id and r.start >= over - 0.01
                           and r.lane != "live"), default=None)
                if nxt is None or nxt - over > 1.0:
                    _add(res, names["end"], sid, clock(sid, over), "nothing started when the show ended"
                         if nxt is None else f"the next item started {nxt - over:.1f} s after the show ended")
        _count(res, names["after"])
        late = [h for h in heard if h.kind != "quiet" and not ctx.in_show(sid, h.t, after=0.3) and not ctx.in_show(sid, h.t + 0.3)
                and h.t > first + 1]
        if late:
            _add(res, names["after"], sid, clock(sid, late[0].t),
                 f"{len(late) * TICK:.1f} s came out of the delay while the stream was not the show's")
