"""What listeners hear of a live show: the broadcast (profanity) delay.

The host's microphone, mixed with the callers on air (live_shows_ws.StationLiveRelay),
goes into a delay line here instead of straight to listeners. Listeners hear it
`target` seconds later, so the host or a producer has that long to press Dump and
cut a word before it airs. See wiki/live-shows.md, "Broadcast delay".

- Pre-roll: when a show starts with a delay, nothing airs until the delay is full.
  The station's music keeps playing meanwhile and the host's first words air the
  moment it stops (no dead air while the delay fills).
- Dump: the newest `dump` seconds still waiting in the delay (all of it when dump
  is 0) are thrown away. Listeners carry on with what was said before.
- Build-back: after a Dump (or a network gap) the delay refills by stretching what
  airs: pauses play at half speed, speech 3 % slower at the same pitch (a cycle of
  the voice is repeated now and then). With the delay too long (after a time check
  aired over the host) it shrinks the same way in reverse.
- Exit: the delay can be wound down to zero (pauses shortened, speech 3 % faster),
  done by itself before a show's hard stop, so the show ends on time.
- Inserts: a time check (or another clip) airs at an exact clock time. The host's
  audio waits in the delay meanwhile and nothing is lost.

Audio is 16-bit mono PCM at RATE. All of it runs in the web process's event loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

logger = logging.getLogger(__name__)

RATE = 24000
TICK = 0.05                 # the pump's step (seconds)
DEFAULT_DELAY = 15.0        # seconds; a station's automation_config.live_delay_seconds
MAX_DELAY = 40.0
CAPACITY = int(RATE * (MAX_DELAY + 60))  # room for a delay plus inserts aired over it

# Input seconds taken per output second while building / shrinking
BUILD_SPEECH, BUILD_PAUSE = 0.97, 0.5
SHRINK_SPEECH, SHRINK_PAUSE = 1.03, 2.0
SHRINK_HURRY = 1.12         # the most speech is sped up, close to a hard stop
BUILD_BELOW = 0.25          # seconds short of the target before building starts
SHRINK_ABOVE = 0.5          # seconds over the target before shrinking starts
QUIET_FLOOR = 300           # RMS at or under this is a pause whatever the voice level
QUIET_SHARE = 0.15          # ... and so is anything under 15 % of the recent speech level
EDGE = int(RATE * 0.01)     # 10 ms fades where a Dump cut the audio
# Before a show's hard stop the delay may only be as long as can still be played out by
# hurrying non-stop speech: this many seconds of delay per second left, less a margin.
# (It was a fixed 3 minutes at up to 12 %: that takes back 21 s, so a 30 or 40 s delay,
# or any show shorter than its delay plus 3 minutes, was cut with words still waiting.)
WIND_RATE = 0.10
SOON = 1.0                  # seconds before first air that `on_soon` is called
WIND_MARGIN = 5.0
CUSHION = int(RATE * 0.3)   # least audio waiting before it plays out (smooth, no stutter)
STATUS_EVERY = 0.5
HEARD_KEPT = int(RATE * 6)    # what aired just before a Dump, kept for the dump log          # seconds between status messages


def settings_for(config: dict | None) -> tuple[float, float, str]:
    """(delay seconds, dump seconds (0 = all of it), timed-items rule) of a station."""
    config = config or {}
    try:
        delay = float(config.get("live_delay_seconds", DEFAULT_DELAY))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY
    try:
        dump = float(config.get("live_dump_seconds", 0) or 0)
    except (TypeError, ValueError):
        dump = 0.0
    rule = config.get("live_timed_items") or "time"
    if rule not in ("time", "all", "none"):
        rule = "time"
    return max(0.0, min(MAX_DELAY, delay)), max(0.0, min(MAX_DELAY, dump)), rule


def filler_asset(config: dict | None) -> str | None:
    """The library item a station plays after a Dump while the delay refills
    (`automation_config.live_dump_filler_asset_id`), or None: the delay then builds
    back slowly by stretching the host's speech."""
    value = (config or {}).get("live_dump_filler_asset_id")
    return str(value) if value else None


def stretch(x: np.ndarray, n: int) -> np.ndarray:
    """`x` played back over `n` samples (linear interpolation)."""
    if n <= 0:
        return np.zeros(0, dtype=np.int16)
    if len(x) == 0:
        return np.zeros(n, dtype=np.int16)
    if len(x) == n:
        return x.astype(np.int16, copy=False)
    if len(x) == 1:
        return np.full(n, x[0], dtype=np.int16)
    pos = np.linspace(0, len(x) - 1, n)
    return np.interp(pos, np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


JOIN = int(RATE * 0.002)    # 2 ms crossfade where a cycle of the voice is repeated or dropped
PERIOD_MIN, PERIOD_MAX = int(RATE / 400), int(RATE / 80)  # a voice's cycle: 80 to 400 Hz


def _voice_period(x: np.ndarray) -> int:
    """The length, in samples, after which this bit of speech best repeats itself."""
    w = x[: min(len(x), 3 * PERIOD_MAX)].astype(np.float32)
    if len(w) < 2 * PERIOD_MAX:
        return PERIOD_MAX
    w = w - w.mean()
    base = w[: len(w) - PERIOD_MAX]
    energy = float(np.dot(base, base)) + 1.0
    lags = np.arange(PERIOD_MIN, PERIOD_MAX + 1)
    scores = np.empty(len(lags), dtype=np.float64)
    for i, lag in enumerate(lags):
        other = w[lag: lag + len(base)]
        scores[i] = float(np.dot(base, other)) / (energy * (float(np.dot(other, other)) + 1.0)) ** 0.5
    # The best lag; of lags about as good (multiples of the cycle), the shortest peak
    top = float(scores.max())
    good = np.flatnonzero(scores >= top - 0.03)
    first = good[0]
    run_end = first
    while run_end + 1 < len(scores) and scores[run_end + 1] >= top - 0.03:
        run_end += 1
    best = int(lags[first + int(np.argmax(scores[first:run_end + 1]))])
    # A few cycles at once when the cycle is short, so one join does more
    reps = max(1, int(RATE * 0.008) // best)
    return best * reps


def _join(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """`a` then `b`, with the first samples of `b` faded in over the end of `a`'s
    continuation (both lie on the same point of the voice's cycle)."""
    out = np.concatenate((a, b)).astype(np.float32)
    k = min(JOIN, len(a), len(b))
    if k > 1:
        # what `a` would have gone on to say is not kept; a short fade hides any step
        step = float(a[-1]) - float(b[0])
        out[len(a): len(a) + k] += step * np.linspace(1.0, 0.0, k, endpoint=False)
    return np.clip(out, -32768, 32767).astype(np.int16)


def _end_tap(tap: asyncio.Queue) -> None:
    """Tell a tap's reader the show is over, making room for the word if it is full."""
    while True:
        try:
            tap.put_nowait(None)
            return
        except asyncio.QueueFull:
            try:
                tap.get_nowait()
            except asyncio.QueueEmpty:
                return


@dataclass
class Insert:
    """A clip that airs at a clock time (epoch seconds), the host waiting meanwhile."""
    key: str
    title: str
    pcm: np.ndarray
    air_at: float
    kind: str = "timed"
    on_start: Callable[[], Awaitable[None]] | None = None
    pos: int = 0
    started: float | None = None

    @property
    def seconds(self) -> float:
        return len(self.pcm) / RATE


@dataclass
class AirStats:
    dumps: int = 0
    dumped_seconds: float = 0.0
    fills: int = 0
    inserts: list = field(default_factory=list)


class LiveAir:
    """One station's delay line and the clock that plays it out."""

    def __init__(
        self,
        station_id: str,
        show_id: str,
        send: Callable[[bytes], Awaitable[None]],
        delay: float = 0.0,
        dump: float = 0.0,
        on_listening: Callable[[bool], Awaitable[None]] | None = None,
        on_soon: Callable[[], Awaitable[None]] | None = None,
        on_status: Callable[[dict], Awaitable[None]] | None = None,
        on_done: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.station_id = station_id
        self.show_id = show_id
        self.send = send
        self.configured = max(0.0, min(MAX_DELAY, delay))
        self.target = self.configured     # what the delay is steering to now (0 = exiting)
        self.dump_size = max(0.0, dump)
        self.on_listening = on_listening
        # Called once, SOON seconds before listeners first hear the show: a stream server
        # needs that long to go quiet (it lagged the host by 2.5 s on Jerusalem)
        self.on_soon = on_soon
        self.told_soon = False
        self.on_closed: Callable[[], Awaitable[None]] | None = None   # always runs at the end
        self.ever_heard = False
        self.on_status = on_status
        self.on_done = on_done
        self.clock = clock

        self.ring = np.zeros(CAPACITY, dtype=np.int16)
        self.read = 0
        self.count = 0
        # Samples ever written, less what Dumps took back: `fed - count` is the place
        # listeners hear now. The show's video rides on it (services/live_video.py).
        self.fed = 0
        self.listening = False      # listeners hear this show now
        # A timed item (the time check) is on air on the station: the show waits for it
        # to end before listeners hear the host, and the host's words wait in the delay
        self.hold_until = 0.0
        self.finishing = False      # show over (or host gone): play out what is left, take no more
        self.finish_reason = ""
        self.closed = False
        self.fade_in_next = False
        self.speech_level = 3000.0  # running RMS of speech, for telling pauses apart
        self.inserts: list[Insert] = []
        self.current: Insert | None = None
        self.mode = "pre-roll"
        self.end_at: float | None = None   # the show's hard stop (epoch)
        self.auto_exit = False             # the delay was wound down for the hard stop
        self.last_input = clock()
        self.stats = AirStats()
        self.task: asyncio.Task | None = None
        self._status_at = 0.0
        self.taps: set[asyncio.Queue] = set()
        self.aired = np.zeros(0, dtype=np.int16)   # the last few seconds listeners heard
        self.last_dump: tuple[np.ndarray, np.ndarray] | None = None
        # After a Dump: a jingle that plays while the delay refills at once. `fill_after`
        # counts the samples still to air before it (what the Dump left in the delay).
        self.filler: np.ndarray | None = None
        self.filler_title = ""
        self.fill_after: int | None = None
        self.debt = 0.0             # samples of stretching owed while speech passes untouched
        self._close_done = False

    # ----- input -----

    def _write(self, pcm: np.ndarray) -> None:
        n = len(pcm)
        if n == 0:
            return
        self.fed += n
        if n > CAPACITY:
            pcm = pcm[-CAPACITY:]
            n = CAPACITY
        overflow = self.count + n - CAPACITY
        if overflow > 0:  # never expected: drop the oldest
            self.read = (self.read + overflow) % CAPACITY
            self.count -= overflow
        start = (self.read + self.count) % CAPACITY
        first = min(n, CAPACITY - start)
        self.ring[start:start + first] = pcm[:first]
        if first < n:
            self.ring[: n - first] = pcm[first:]
        self.count += n

    def _peek(self, n: int) -> np.ndarray:
        n = min(n, self.count)
        first = min(n, CAPACITY - self.read)
        if first == n:
            return self.ring[self.read:self.read + n].copy()
        return np.concatenate((self.ring[self.read:self.read + first], self.ring[: n - first]))

    def _pop(self, n: int) -> np.ndarray:
        out = self._peek(n)
        self.read = (self.read + len(out)) % CAPACITY
        self.count -= len(out)
        return out

    def passthrough(self) -> bool:
        """No delay, nothing waiting and nothing inserted: frames can go straight out."""
        return (self.listening and not self.finishing and self.target == 0 and self.count == 0
                and self.current is None and not self._insert_due())

    def feed(self, frame: bytes | np.ndarray) -> None:
        """The host's audio (callers mixed in) for the delay."""
        if self.finishing or self.closed:
            return
        pcm = frame if isinstance(frame, np.ndarray) else np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2")
        if self.fade_in_next and len(pcm):
            pcm = pcm.astype(np.float32)
            k = min(EDGE, len(pcm))
            pcm[:k] *= np.linspace(0.0, 1.0, k)
            pcm = pcm.astype(np.int16)
            self.fade_in_next = False
        self.last_input = self.clock()
        self._write(pcm.astype(np.int16, copy=False))

    # ----- operator actions -----

    @property
    def buffered(self) -> float:
        return self.count / RATE

    def dump(self) -> float:
        """Throw away the newest audio still waiting in the delay. Returns seconds cut."""
        if self.count <= 0:
            return 0.0
        size = self.dump_size if self.dump_size > 0 else self.buffered
        k = min(self.count, int(size * RATE))
        if k <= 0:
            return 0.0
        # Kept for the dump log: what was cut, and what listeners heard just before
        idx = (self.read + self.count - k + np.arange(k)) % CAPACITY
        self.last_dump = (self.aired.copy(), self.ring[idx].copy())
        self.count -= k
        self.fed -= k
        # A short fade on the end of what stays, and on the start of what comes next
        tail = min(EDGE, self.count)
        if tail:
            idx = (self.read + self.count - tail + np.arange(tail)) % CAPACITY
            self.ring[idx] = (self.ring[idx].astype(np.float32) * np.linspace(1.0, 0.0, tail)).astype(np.int16)
        self.fade_in_next = True
        cut = k / RATE
        self.stats.dumps += 1
        self.stats.dumped_seconds += cut
        logger.info("Live air %s: Dump cut %.1f s (%.1f s left in the delay)", self.station_id, cut, self.buffered)
        if self.filler is not None and self.target > 0 and not self.finishing:
            # What the Dump left airs first, then the filler exactly where the cut is
            self.fill_after = self.count
        return cut

    def exit_delay(self) -> None:
        """Wind the delay down to zero (pauses shortened, speech a little faster)."""
        self.target = 0.0
        self.fill_after = None

    def enter_delay(self, seconds: float | None = None) -> None:
        """Build the delay (back) up to `seconds`, or the station's setting."""
        if seconds is not None:
            self.configured = max(0.0, min(MAX_DELAY, float(seconds)))
        self.target = self.configured
        self.auto_exit = False

    def add_insert(self, insert: Insert) -> bool:
        if any(i.key == insert.key for i in self.inserts) or (self.current and self.current.key == insert.key):
            return False
        self.inserts.append(insert)
        self.inserts.sort(key=lambda i: i.air_at)
        return True

    def finish(self, reason: str = "ended") -> None:
        """No more input: play out what is waiting (wound down quickly), then stop."""
        self.finishing = True
        self.finish_reason = reason
        self.target = 0.0

    # ----- output -----

    def _insert_due(self) -> bool:
        return bool(self.inserts) and self.inserts[0].air_at <= self.clock()

    def _is_pause(self, x: np.ndarray) -> bool:
        if len(x) == 0:
            return True
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
        quiet = rms <= QUIET_FLOOR or rms < QUIET_SHARE * self.speech_level
        if not quiet:
            self.speech_level = 0.95 * self.speech_level + 0.05 * rms
        return quiet

    def _shrink_speech(self) -> float:
        """How fast speech plays while the delay shrinks: 3 % as a rule, more (up to
        12 %) when the show's hard stop is near and the delay would not be empty by then."""
        if self.auto_exit and self.end_at is not None and not self.finishing:
            left = self.end_at - self.clock() - WIND_MARGIN
            if left <= 0:
                return SHRINK_HURRY
            return min(SHRINK_HURRY, max(SHRINK_SPEECH, 1.0 + 1.1 * self.buffered / left))
        return SHRINK_SPEECH

    def _program(self, n: int) -> np.ndarray:
        """`n` samples of the delayed show, stretched or squeezed toward the target."""
        target = int(self.target * RATE)
        drift = self.count - target
        if self.target == 0:
            shrink, build = self.count > 0, False
        else:
            shrink = drift > SHRINK_ABOVE * RATE
            build = drift < -BUILD_BELOW * RATE and self.fill_after is None
        if self.target > 0 and self.count < CUSHION:
            # Too little waiting to play smoothly (just after a Dump of everything, or the
            # host's audio is late): a moment of silence while it gathers, never stutter
            self.mode = "building"
            return np.zeros(n, dtype=np.int16)
        if self.target == 0 and self.count <= CUSHION and not self.finishing:
            # Caught up: what is left goes out as it is and frames pass straight through
            self.mode = "no delay"
            return self._pop(self.count)
        if self.count == 0:
            return np.zeros(0 if self.finishing else n, dtype=np.int16)
        probe = self._peek(n)
        pause = self._is_pause(probe)
        if build:
            self.mode = "building"
        elif shrink:
            self.mode = "exiting" if self.target == 0 else "shrinking"
        else:
            self.mode = "steady" if self.target > 0 else "no delay"
        if not (build or shrink):
            self.debt = 0.0
            return self._take(n, n)
        if pause:
            # A pause has no pitch to keep: it is simply played slower or faster
            want = max(1, int(round(n * (BUILD_PAUSE if build else SHRINK_PAUSE))))
            if shrink:
                room = self.count - target if self.target > 0 else self.count
                want = max(1, min(want, n + room if self.target > 0 else room))
            return self._take(want, n)
        # Speech keeps its pitch: now and then one cycle of the voice is said twice
        # (building) or left out (shrinking), joined where the wave repeats.
        speed = BUILD_SPEECH if build else self._shrink_speech()
        self.debt += abs(1.0 - speed) * n
        period = _voice_period(probe)
        if self.debt < period or n < 2 * period + 2 * JOIN:
            return self._take(n, n)
        if build:
            x = self._pop(n - period) if self.count >= n - period else None
            if x is None:
                return self._take(n, n)
            self.debt -= period
            m = (len(x) + period) // 2
            return _join(x[:m], x[m - period:])
        if self.count < n + period or (self.target > 0 and self.count - period < target):
            return self._take(n, n)
        x = self._pop(n + period)
        self.debt -= period
        m = n // 2
        return _join(x[:m], x[m + period:])

    def _take(self, want: int, n: int) -> np.ndarray:
        """`want` samples from the delay, played over `n`."""
        if want > self.count:
            if self.finishing:
                return self._pop(self.count)  # the last of the show
            # Not enough waiting (the host's audio is late): what there is, then silence
            got = self._pop(self.count)
            out = np.zeros(n, dtype=np.int16)
            out[: min(n, len(got))] = got[:n]
            return out
        return stretch(self._pop(want), n)

    def _start_fill(self) -> None:
        """Play the filler for as long as the delay is short: while it airs the host's
        words gather, so the delay is whole again when it ends."""
        self.fill_after = None
        want = int(self.target * RATE) - self.count
        if self.filler is None or self.finishing or want < RATE // 2:
            return
        pcm = self.filler[:want].copy()
        fade = min(len(pcm), RATE // 3)
        if fade and len(self.filler) > want:
            # Cut short: fade it out, never a chopped jingle
            pcm[-fade:] = (pcm[-fade:].astype(np.float32) * np.linspace(1.0, 0.0, fade)).astype(np.int16)
        self.stats.fills += 1
        self.current = Insert(key=f"fill:{self.stats.dumps}:{self.stats.fills}", title=self.filler_title or "Filler",
                              pcm=pcm, air_at=self.clock(), kind="filler", started=self.clock())
        logger.info("Live air %s: filler '%s' for %.1f s while the delay refills",
                    self.station_id, self.filler_title, len(pcm) / RATE)

    def produce(self, n: int) -> np.ndarray | None:
        """The next `n` samples listeners hear (None while nothing airs yet)."""
        if self.end_at is not None and not self.finishing and self.target > 0:
            room = max(0.0, (self.end_at - self.clock() - WIND_MARGIN) * WIND_RATE)
            if room < self.target:
                self.target = room if room >= 0.3 else 0.0
                self.auto_exit = True
        if not self.listening:
            if self.finishing:
                return None
            if self.count >= int(self.target * RATE) and (self.count > 0 or self.target == 0) \
                    and self.clock() >= self.hold_until:
                self.listening = True
                self.ever_heard = True
                self.mode = "steady" if self.target > 0 else "no delay"
                self._notify_listening(True)
            else:
                self.mode = "pre-roll"
                if not self.told_soon and self.on_soon is not None and self.count > 0 \
                        and self.count >= int((self.target - SOON) * RATE) \
                        and self.clock() >= self.hold_until - SOON:
                    self.told_soon = True
                    asyncio.ensure_future(self._safe(self.on_soon()))
                return None
        out = np.zeros(0, dtype=np.int16)
        while len(out) < n:
            need = n - len(out)
            if self.current is None and self._insert_due():
                self.current = self.inserts.pop(0)
                self.current.started = self.clock()
                self.stats.inserts.append({"key": self.current.key, "title": self.current.title,
                                           "at": self.current.started})
                if self.current.on_start is not None:
                    asyncio.ensure_future(self._safe(self.current.on_start()))
            if self.current is not None:
                part = self.current.pcm[self.current.pos:self.current.pos + need]
                self.current.pos += len(part)
                if self.current.pos >= len(self.current.pcm):
                    self.current = None
                self.mode = "insert"
                out = np.concatenate((out, part.astype(np.int16, copy=False)))
                continue
            if self.fill_after is not None:
                if self.fill_after <= 0 or self.count < CUSHION or self.target <= 0:
                    self._start_fill()
                    if self.current is not None:
                        continue
                else:
                    need = min(need, self.fill_after)
            had = self.count
            part = self._program(need)
            if self.fill_after is not None:
                self.fill_after -= max(1, had - self.count)
            out = np.concatenate((out, part))
            if len(part) < need:
                break  # caught up, or the end of the show
        self._remember(out)
        return out

    def _remember(self, out: np.ndarray) -> None:
        self.aired = np.concatenate((self.aired, out))[-HEARD_KEPT:]
        if self.taps and len(out):
            data = out.astype("<i2").tobytes()
            for tap in list(self.taps):
                try:
                    tap.put_nowait(data)
                except asyncio.QueueFull:
                    self.taps.discard(tap)   # a reader that fell 20 s behind is let go
                    _end_tap(tap)

    def tap(self) -> asyncio.Queue:
        """A copy of everything listeners hear from now on, as PCM chunks; None = over.
        (The playout engine's stream takes the show from here: /playout/{id}/live.mp3.)"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=400)
        if self.closed:
            queue.put_nowait(None)
        else:
            self.taps.add(queue)
        return queue

    def untap(self, queue: asyncio.Queue) -> None:
        self.taps.discard(queue)

    def passed(self, frame: bytes) -> None:
        """A frame that went straight to listeners (no delay): remembered like the rest.
        It counts as the host sending: with no delay the station was taken from a host
        in mid sentence after `live_host_gone_seconds` (browser test, 2026-11-18)."""
        self.last_input = self.clock()
        self._remember(np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2"))

    def drained(self) -> bool:
        return self.finishing and self.count == 0 and self.current is None

    # ----- the clock -----

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        sent = 0
        try:
            while not self.closed:
                await asyncio.sleep(TICK)
                if self.drained() or (self.finishing and not self.listening):
                    break
                due = int((loop.time() - t0) * RATE) - sent
                if due <= 0:
                    continue
                if due > RATE:  # the loop stalled: do not burst a backlog out
                    t0 = loop.time() - sent / RATE - TICK
                    due = int(RATE * TICK)
                sent += due
                out = self.produce(due)
                if out is not None and len(out):
                    try:
                        await self.send(out.astype("<i2").tobytes())
                    except Exception as e:
                        logger.debug("Live air %s: send failed: %s", self.station_id, e)
                await self._maybe_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live air %s stopped", self.station_id)
        finally:
            await self._close()

    async def _close(self) -> None:
        if self._close_done:
            return
        self._close_done = True
        self.closed = True
        for tap in list(self.taps):
            _end_tap(tap)
        self.taps.clear()
        self.mode = "off"
        if self.listening:
            self.listening = False
            if self.on_listening is not None:
                await self._safe(self.on_listening(False))
        await self._maybe_status(force=True)
        if self.on_closed is not None:
            await self._safe(self.on_closed())
        if self.on_done is not None:
            await self._safe(self.on_done())

    async def stop(self) -> None:
        """Stop at once (nothing more airs)."""
        self.closed = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close()

    def _notify_listening(self, on: bool) -> None:
        if self.on_listening is not None:
            asyncio.ensure_future(self._safe(self.on_listening(on)))

    @staticmethod
    async def _safe(aw) -> None:
        try:
            await aw
        except Exception:
            logger.exception("Live air callback failed")

    def status(self) -> dict:
        now = self.clock()
        nxt = self.inserts[0] if self.inserts else None
        safe_size = self.dump_size if self.dump_size > 0 else self.configured
        return {
            "station_id": self.station_id,
            "show_id": self.show_id,
            "mode": self.mode,
            "on_air": self.listening,
            # Seconds the show still waits for a timed item on the station to end
            "hold": 0.0 if self.listening else round(max(0.0, self.hold_until - now), 1),
            "delay": round(self.buffered, 2),
            "target": self.target,
            "configured": self.configured,
            "dump_size": self.dump_size,
            # A Dump now cuts as much as one is meant to
            "safe": self.configured > 0 and self.buffered + 0.5 >= min(safe_size, self.configured),
            "finishing": self.finishing,
            "auto_exit": self.auto_exit,
            "dumps": self.stats.dumps,
            "insert": ({"title": self.current.title, "kind": self.current.kind,
                        "remaining": round((len(self.current.pcm) - self.current.pos) / RATE, 1)}
                       if self.current else None),
            "next_insert": ({"title": nxt.title, "kind": nxt.kind, "in": round(nxt.air_at - now, 1)}
                            if nxt else None),
            "filler": self.filler_title if self.filler is not None else None,
            "host_silent_for": round(now - self.last_input, 1),
        }

    async def _maybe_status(self, force: bool = False) -> None:
        if self.on_status is None:
            return
        now = time.monotonic()
        if not force and now - self._status_at < STATUS_EVERY:
            return
        self._status_at = now
        await self._safe(self.on_status(self.status()))
