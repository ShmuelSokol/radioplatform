"""When each item last aired on a station, kept in memory.

The rotation pickers (playlists, program blocks) and the control room library ask
"when did each item last air here?". Answering from the station's whole play
history is a GROUP BY that grows with every play: 16 to 17 s for one station on
the live database (2026-11-18), and it ran inside requests (a Regenerate, a
queue refill at the top of the hour).

So no request reads the whole history. The first call for a station reads the
last FIRST_READ_DAYS as plain rows, later calls only the plays since the last
read (both use the (station_id, start_utc) index and take milliseconds). The
whole history is read in the background, in its own session, one station at a
time: once after a station's first read, then daily. `warm_all` makes the first
read at startup, so the background read has usually finished before any picker
asks.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

# Plays are written when they start, a little after their start_utc; re-read this
# much before the last read so none fall between two reads.
OVERLAP = timedelta(minutes=10)
# The whole history is read in the background only: once after the first read
# of a station, then daily. It took 16 to 17 s for one station on the live
# database (2026-11-18), so no request may wait for it, and stations take turns.
FULL_RELOAD_SECONDS = 24 * 3600.0
# What the first read of a station covers. Enough to rank every song that has
# aired lately; anything older counts as "not aired" until the background read
# has filled it in a few seconds later.
FIRST_READ_DAYS = 30
# False in the tests (conftest): the first read is then the whole history, in
# the caller's session, so a test sees every play at once.
BACKGROUND_FULL_READ = True
# A failed whole-history read is tried again after this long
RETRY_SECONDS = 600.0
# A failed startup read of a station is tried again after these waits (a
# minute and a half in all). If all fail, the first request that asks makes the
# same bounded read itself.
WARM_RETRY_WAITS = (2.0, 8.0, 20.0, 60.0)
# A whole-history read slower than this logs the database's plan for it, once per process
EXPLAIN_OVER_SECONDS = 3.0


class _Entry:
    __slots__ = ("last", "read_at", "full_at")

    def __init__(self) -> None:
        self.last: dict[str, datetime] = {}
        self.read_at: datetime | None = None
        self.full_at: float | None = None


_cache: dict[str, _Entry] = {}
_locks: dict[str, asyncio.Lock] = {}


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def reset() -> None:
    global _reload_lock
    _cache.clear()
    _locks.clear()
    _reload_lock = None


def _full_query(station_id):
    from app.models.play_log import PlayLog

    return (
        select(PlayLog.asset_id, func.max(PlayLog.start_utc))
        .where(PlayLog.station_id == station_id, PlayLog.asset_id.isnot(None))
        .group_by(PlayLog.asset_id)
    )


def _merge(last: dict, rows) -> None:
    for asset_id, when in rows:
        if when is None or asset_id is None:
            continue
        key, t = str(asset_id), _utc(when)
        if key not in last or last[key] < t:
            last[key] = t


def _recent_query(station_id, since: datetime):
    """Plays since `since`, one row each. No GROUP BY on purpose: with it the
    planner may walk the station's whole (station, asset, start) index to get
    the groups in order, which is the 16 s read; plain rows by start time use
    the (station_id, start_utc) index and come back in milliseconds."""
    from app.models.play_log import PlayLog

    return select(PlayLog.asset_id, PlayLog.start_utc).where(
        PlayLog.station_id == station_id, PlayLog.start_utc >= since
    )


async def last_played(db, station_id) -> dict[str, datetime]:
    """asset id (str) -> last start time on this station. A copy; callers may change it.

    Never reads the whole history: the first call for a station reads the last
    FIRST_READ_DAYS, later calls the plays since the last read. Older plays are
    filled in by the background read (`_reload`), started by the first call."""
    sid = str(station_id)
    lock = _locks.setdefault(sid, asyncio.Lock())
    async with lock:
        entry = _cache.get(sid)
        now = datetime.now(timezone.utc)
        if entry is None and not BACKGROUND_FULL_READ:
            rows = (await db.execute(_full_query(station_id))).all()
            entry = _Entry()
            entry.full_at = time.monotonic()
        else:
            since = now - timedelta(days=FIRST_READ_DAYS) if entry is None else entry.read_at - OVERLAP
            rows = (await db.execute(_recent_query(station_id, since))).all()
            if entry is None:
                entry = _Entry()  # full_at None: the whole history has not been read yet
        _merge(entry.last, rows)
        entry.read_at = now
        _cache[sid] = entry
        if entry.full_at is None or time.monotonic() - entry.full_at > FULL_RELOAD_SECONDS:
            entry.full_at = time.monotonic()  # one re-read at a time
            _in_background(_reload(station_id))
        return dict(entry.last)


_tasks: set[asyncio.Task] = set()
_reload_lock: asyncio.Lock | None = None


def _reload_turn() -> asyncio.Lock:
    """One whole-history read at a time, so three stations starting together
    do not run three 16 s queries side by side."""
    global _reload_lock
    if _reload_lock is None:
        _reload_lock = asyncio.Lock()
    return _reload_lock


def _in_background(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _reload(station_id) -> None:
    """The whole history again, in its own session; then swapped in, keeping any
    play that was merged in while the read ran. A failed read (the live database
    drops connections now and then) is tried again after RETRY_SECONDS."""
    import logging

    from app.db.engine import async_session_factory

    log = logging.getLogger(__name__)
    sid = str(station_id)
    try:
        async with _reload_turn():
            began = datetime.now(timezone.utc)
            started = time.monotonic()
            async with async_session_factory() as session:
                await session.connection()  # timed apart: a new connection costs about 2 s
                connected = time.monotonic()
                # The first read after a start, run by the database with its own
                # stopwatch (see _explain); the real read follows it
                await _explain(session, station_id)
                explained = time.monotonic() - connected
                rows = (await session.execute(_full_query(station_id))).all()
                took = time.monotonic() - started
                if took > EXPLAIN_OVER_SECONDS:
                    from app.core import loop_watch
                    from app.db.engine import pool_state

                    log.warning(
                        "Last-played whole-history read for station %s: %.1f s (connection %.1f s, "
                        "the database's own timed run %.1f s, query and %d rows %.1f s; loop held %.1f s of it; %s)",
                        sid, took, connected - started, explained, len(rows),
                        took - explained - (connected - started),
                        loop_watch.stalled_since(started), pool_state(),
                    )
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
        log.info("Last-played whole history read for station %s: %d items, %.1f s", sid, len(fresh), took)
    except Exception:
        log.warning("Last-played whole-history read failed for station %s", sid, exc_info=True)
        entry = _cache.get(sid)
        if entry is not None:
            entry.full_at = time.monotonic() - FULL_RELOAD_SECONDS + RETRY_SECONDS


_explained = False


async def _explain(session, station_id) -> None:
    """Once per process, as the first whole-history read after a start: the
    database runs the read under EXPLAIN (ANALYZE, BUFFERS) and the plan, its
    own time and what it had to fetch from disk ("read=", against "hit=" for
    memory) go to the log with the table's indexes and sizes.

    Nobody has SQL access to the live database. On 2026-11-18 the first read
    after each start took 12 to 16 s while the same read a moment later took
    0.13 s by the database's own clock, with the connection, the pool and the
    event loop ruled out; this shows whether those seconds are spent inside the
    database (a cold read from disk) or on the way to it."""
    global _explained
    if _explained:
        return
    _explained = True
    import logging

    from sqlalchemy import text

    log = logging.getLogger(__name__)
    try:
        if session.bind.dialect.name != "postgresql":
            return
        began = time.monotonic()
        plan = (await session.execute(text(
            "EXPLAIN (ANALYZE, BUFFERS) SELECT asset_id, max(start_utc) FROM play_logs"
            " WHERE station_id = :sid AND asset_id IS NOT NULL GROUP BY asset_id"
        ), {"sid": station_id})).all()
        plan_took = time.monotonic() - began
        indexes = (await session.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'play_logs'"
        ))).all()
        size = (await session.execute(text(
            "SELECT pg_size_pretty(pg_table_size('play_logs')), pg_size_pretty(pg_indexes_size('play_logs')),"
            " (SELECT reltuples::bigint FROM pg_class WHERE relname = 'play_logs')"
        ))).first()
        log.warning(
            "LAST-PLAYED DIAGNOSIS: the first whole-history read after this start, timed by the database, "
            "took %.1f s as seen from here. Table %s, indexes %s, about %s rows. Indexes: %s. Plan: %s",
            plan_took, size[0], size[1], size[2],
            "; ".join(f"{name}: {definition}" for name, definition in indexes),
            " / ".join(row[0] for row in plan),
        )
    except Exception:
        log.warning("Last-played diagnosis failed", exc_info=True)


async def warm_all() -> None:
    """At startup: the first (bounded) read of every active station, which also
    starts its whole-history read in the background. Each station on its own, so
    one failure does not leave the others for a request to read."""
    import logging

    from app.db.engine import async_session_factory
    from app.models.station import Station

    log = logging.getLogger(__name__)
    try:
        async with async_session_factory() as session:
            ids = (await session.execute(select(Station.id).where(Station.is_active.is_(True)))).scalars().all()
    except Exception:
        log.warning("Last-played warm-up could not list the stations", exc_info=True)
        return
    for station_id in ids:
        if str(station_id) in _cache:
            continue
        for attempt, wait in enumerate(WARM_RETRY_WAITS, start=1):
            if str(station_id) in _cache:
                break  # a request read it meanwhile
            try:
                began = time.monotonic()
                async with async_session_factory() as session:
                    known = await last_played(session, station_id)
                log.info(
                    "Last-played warm-up read station %s: %d items aired in the last %d days, %.1f s",
                    station_id, len(known), FIRST_READ_DAYS, time.monotonic() - began,
                )
                break
            except Exception as e:
                # "connection was closed in the middle of operation" left every
                # station unread after the 08:35Z restart on 2026-11-18; three
                # tries inside 6 s all met a refused connection at 09:31Z
                log.warning(
                    "Last-played warm-up failed for station %s (attempt %d of %d): %s",
                    station_id, attempt, len(WARM_RETRY_WAITS), e,
                )
                await asyncio.sleep(wait)
