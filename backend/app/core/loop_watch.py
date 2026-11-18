"""Says when the one event loop is held up, and by what.

Every request, the scheduler and the background jobs share one loop. Python that
runs for seconds without an `await` stops all of them, and from the outside that
looks like a slow database: on 2026-11-18 a query the database ran in 129 ms
took 12.0 s as seen by the app, and nothing said where the time went.

A task on the loop notes the time ten times a second. A thread watches that
note; when it is over STALL_SECONDS old the thread logs what the loop's thread is
running at that moment ("EVENT LOOP HELD ..."), and the length once it moves again.
`stalled_since(t)` lets other log lines (SLOW QUERY) say how much of their wait
was the loop and not the database.
"""
import asyncio
import logging
import sys
import threading
import time
import traceback
from collections import deque

logger = logging.getLogger(__name__)

BEAT_SECONDS = 0.1
STALL_SECONDS = 1.0
CHECK_SECONDS = 0.25

_beat = time.monotonic()
_loop_thread_id: int | None = None
# (ended_at, seconds) of recent hold-ups, any over 0.2 s
_stalls: deque[tuple[float, float]] = deque(maxlen=200)
_stop = threading.Event()
_thread: threading.Thread | None = None
_task: asyncio.Task | None = None


def stalled_since(since: float) -> float:
    """Seconds the loop was held up after `since` (time.monotonic())."""
    return sum(seconds for ended, seconds in list(_stalls) if ended >= since)


async def _beat_loop() -> None:
    global _beat
    while True:
        await asyncio.sleep(BEAT_SECONDS)
        now = time.monotonic()
        late = now - _beat - BEAT_SECONDS
        if late > 0.2:
            _stalls.append((now, late))
        _beat = now


def _where() -> str:
    frame = sys._current_frames().get(_loop_thread_id)
    if frame is None:
        return "unknown"
    lines = traceback.format_stack(frame)[-8:]
    return " <- ".join(
        " ".join(line.split()) for line in reversed(lines)
    )[:2000]


def _watch() -> None:
    reported_for: float | None = None
    while not _stop.wait(CHECK_SECONDS):
        beat = _beat
        age = time.monotonic() - beat
        if age > STALL_SECONDS and reported_for != beat:
            reported_for = beat
            try:
                logger.warning("EVENT LOOP HELD for %.1f s so far by: %s", age, _where())
            except Exception:
                pass
        elif reported_for is not None and beat != reported_for:
            last = _stalls[-1][1] if _stalls else 0.0
            logger.warning("EVENT LOOP free again after %.1f s", last)
            reported_for = None


def start() -> None:
    """Call from the running loop, once."""
    global _thread, _task, _loop_thread_id, _beat
    if _thread is not None and _thread.is_alive():
        return
    _loop_thread_id = threading.get_ident()
    _beat = time.monotonic()
    _stop.clear()
    _task = asyncio.get_running_loop().create_task(_beat_loop())
    _thread = threading.Thread(target=_watch, name="loop-watch", daemon=True)
    _thread.start()


def stop() -> None:
    global _thread, _task
    _stop.set()
    if _task is not None:
        _task.cancel()
        _task = None
    _thread = None
