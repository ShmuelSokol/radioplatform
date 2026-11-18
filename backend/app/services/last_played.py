"""When each item last aired on a station, kept in memory.

The rotation pickers (playlists, program blocks) and the control room library ask
"when did each item last air here?" with a GROUP BY over the station's whole play
history. That read grows with every play and took 1.3 s at startup and 3.3 s in
the burst after a deploy. Here it is read in full once per station per process,
then only the plays since the last read are fetched (the (station_id, start_utc)
index answers that in milliseconds) and merged in. A full re-read every
FULL_RELOAD_SECONDS picks up anything written with an older start time.

No request waits for the full read any more (2026-11-18: it took 7.35 s inside a
Regenerate three minutes after a restart, and once an hour whichever request
came first paid for it, a song change's top-up included). `warm_all` reads every
active station in the background at startup, and the hourly re-read runs in the
background in its own session while requests keep using what is known. Only a
request that arrives before the warm-up has reached its station reads in full.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

# Plays are written when they start, a little after their start_utc; re-read this
# much before the last read so none fall between two reads.
OVERLAP = timedelta(minutes=10)
FULL_RELOAD_SECONDS = 3600.0


class _Entry:
    __slots__ = ("last", "read_at", "full_at")

    def __init__(self) -> None:
        self.last: dict[str, datetime] = {}
        self.read_at: datetime | None = None
        self.full_at = 0.0


_cache: dict[str, _Entry] = {}
_locks: dict[str, asyncio.Lock] = {}


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def reset() -> None:
    _cache.clear()
    _locks.clear()


def _full_query(station_id):
    from app.models.play_log import PlayLog

    return (
        select(PlayLog.asset_id, func.max(PlayLog.start_utc))
        .where(PlayLog.station_id == station_id, PlayLog.asset_id.isnot(None))
        .group_by(PlayLog.asset_id)
    )


def _merge(last: dict, rows) -> None:
    for asset_id, when in rows:
        if when is None:
            continue
        key, t = str(asset_id), _utc(when)
        if key not in last or last[key] < t:
            last[key] = t


async def last_played(db, station_id) -> dict[str, datetime]:
    """asset id (str) -> last start time on this station. A copy; callers may change it."""
    from app.models.play_log import PlayLog

    sid = str(station_id)
    lock = _locks.setdefault(sid, asyncio.Lock())
    async with lock:
        entry = _cache.get(sid)
        now = datetime.now(timezone.utc)
        stmt = _full_query(station_id)
        if entry is not None:
            stmt = stmt.where(PlayLog.start_utc >= entry.read_at - OVERLAP)
        rows = (await db.execute(stmt)).all()
        if entry is None:
            entry = _Entry()
            entry.full_at = time.monotonic()
        _merge(entry.last, rows)
        entry.read_at = now
        _cache[sid] = entry
        if time.monotonic() - entry.full_at > FULL_RELOAD_SECONDS:
            entry.full_at = time.monotonic()  # one re-read at a time
            _in_background(_reload(station_id))
        return dict(entry.last)


_tasks: set[asyncio.Task] = set()


def _in_background(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _reload(station_id) -> None:
    """The whole history again, in its own session; then swapped in, keeping any
    play that was merged in while the read ran."""
    import logging

    from app.db.engine import async_session_factory

    sid = str(station_id)
    try:
        began = datetime.now(timezone.utc)
        async with async_session_factory() as session:
            rows = (await session.execute(_full_query(station_id))).all()
        fresh: dict[str, datetime] = {}
        _merge(fresh, rows)
        async with _locks.setdefault(sid, asyncio.Lock()):
            entry = _cache.get(sid)
            if entry is None:
                entry = _cache[sid] = _Entry()
                entry.read_at = began
            for key, t in entry.last.items():
                if t >= began - OVERLAP and (key not in fresh or fresh[key] < t):
                    fresh[key] = t
            entry.last = fresh
            entry.full_at = time.monotonic()
    except Exception:
        logging.getLogger(__name__).warning("Last-played re-read failed for station %s", sid, exc_info=True)


async def warm_all() -> None:
    """At startup: read every active station's history before a request needs it."""
    import logging

    from app.db.engine import async_session_factory
    from app.models.station import Station

    try:
        async with async_session_factory() as session:
            ids = (await session.execute(select(Station.id).where(Station.is_active.is_(True)))).scalars().all()
            for station_id in ids:
                if str(station_id) not in _cache:
                    await last_played(session, station_id)
    except Exception:
        logging.getLogger(__name__).warning("Last-played warm-up failed", exc_info=True)
