"""Commands to an engine that cannot be reached from outside (a local FM box).

The stream server's engine takes commands over HTTPS (`PLAYOUT_ENGINE_URL`). A box in a
transmitter room has no inbound line, so its relay (`infra/local/kbr_relay.py`) calls
out instead: it holds `POST /playout/{station}/commands/poll` open, receives each
command the moment it is made, gives it to the engine beside it and sends the engine's
answer with its next poll. While a relay is polling for a station, `playout_engine`
sends that station's commands this way. See `wiki/local-fm-box.md`.

Kept in this process's memory, like the rest of the engine state.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

# A relay that has not polled for this long is gone: commands go by HTTPS again
CONNECTED_SECONDS = 40.0
MAX_WAIT_SECONDS = 25.0


@dataclass
class _Tunnel:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    waiting: dict[str, asyncio.Future] = field(default_factory=dict)
    seen_at: float = 0.0
    polling: int = 0
    routed: bool = False  # last routing reported in the log


_tunnels: dict[str, _Tunnel] = {}


def _tunnel(station_id) -> _Tunnel:
    return _tunnels.setdefault(str(station_id), _Tunnel())


def reset() -> None:
    _tunnels.clear()


def allowed(station_id) -> bool:
    """Only a station named in PLAYOUT_RELAY_STATION_ID may take its commands by relay."""
    return str(station_id) in settings.relay_station_ids


def connected(station_id) -> bool:
    """True while this station's commands go to a relay INSTEAD of PLAYOUT_ENGINE_URL."""
    t = _tunnels.get(str(station_id))
    if t is None or not allowed(station_id):
        return False
    now = t.polling > 0 or time.monotonic() - t.seen_at < CONNECTED_SECONDS
    if now != t.routed:
        t.routed = now
        if now:
            logger.warning("Station %s: engine commands now go to its local box's relay, "
                           "none to PLAYOUT_ENGINE_URL", station_id)
        else:
            logger.warning("Station %s: its local box's relay stopped polling; engine "
                           "commands go to PLAYOUT_ENGINE_URL again", station_id)
    return now


async def send(station_id, path: str, payload: dict, timeout: float = 4.0) -> tuple[int, dict | None] | None:
    """Give the relay a command for its engine and wait for the engine's answer:
    (HTTP status, JSON body), or None when none came in time. A command not picked up
    in time is withdrawn: a Next must not arrive half a minute late."""
    t = _tunnel(station_id)
    cid = uuid.uuid4().hex
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    t.waiting[cid] = fut
    t.queue.put_nowait({"id": cid, "path": path, "payload": payload, "expires": time.monotonic() + timeout})
    try:
        return await asyncio.wait_for(fut, timeout)
    except TimeoutError:
        return None
    finally:
        t.waiting.pop(cid, None)


def answer(station_id, cid: str, status: int, body: dict | None) -> None:
    fut = _tunnel(station_id).waiting.get(cid)
    if fut is not None and not fut.done():
        fut.set_result((int(status), body if isinstance(body, dict) else None))


async def poll(station_id, wait: float) -> list[dict]:
    """Commands waiting for this station's engine; holds the call up to `wait` seconds
    until one is made."""
    t = _tunnel(station_id)
    t.seen_at = time.monotonic()
    t.polling += 1
    connected(station_id)  # logs the switch the moment a relay appears
    try:
        out: list[dict] = []
        deadline = time.monotonic() + max(0.0, min(float(wait), MAX_WAIT_SECONDS))
        while True:
            while not t.queue.empty():
                cmd = t.queue.get_nowait()
                # Withdrawn (its sender gave up) or too old: never delivered late
                if cmd["id"] in t.waiting and cmd["expires"] > time.monotonic():
                    out.append({"id": cmd["id"], "path": cmd["path"], "payload": cmd["payload"]})
            left = deadline - time.monotonic()
            if out or left <= 0:
                return out
            try:
                t.queue.put_nowait(await asyncio.wait_for(t.queue.get(), left))
            except TimeoutError:
                return out
    finally:
        t.polling -= 1
        t.seen_at = time.monotonic()
