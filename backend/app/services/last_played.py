"""When each item last aired on a station, kept in memory.

The rotation pickers (playlists, program blocks) and the control room library ask
"when did each item last air here?" with a GROUP BY over the station's whole play
history. That read grows with every play and took 1.3 s at startup and 3.3 s in
the burst after a deploy. Here it is read in full once per station per process,
then only the plays since the last read are fetched (the (station_id, start_utc)
index answers that in milliseconds) and merged in. A full re-read every
FULL_RELOAD_SECONDS picks up anything written with an older start time.
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


async def last_played(db, station_id) -> dict[str, datetime]:
    """asset id (str) -> last start time on this station. A copy; callers may change it."""
    from app.models.play_log import PlayLog

    sid = str(station_id)
    lock = _locks.setdefault(sid, asyncio.Lock())
    async with lock:
        entry = _cache.get(sid)
        now = datetime.now(timezone.utc)
        full = entry is None or time.monotonic() - entry.full_at > FULL_RELOAD_SECONDS
        stmt = (
            select(PlayLog.asset_id, func.max(PlayLog.start_utc))
            .where(PlayLog.station_id == station_id, PlayLog.asset_id.isnot(None))
            .group_by(PlayLog.asset_id)
        )
        if not full:
            stmt = stmt.where(PlayLog.start_utc >= entry.read_at - OVERLAP)
        rows = (await db.execute(stmt)).all()
        if full:
            entry = _Entry()
            entry.full_at = time.monotonic()
        for asset_id, when in rows:
            if when is None:
                continue
            key, t = str(asset_id), _utc(when)
            if key not in entry.last or entry.last[key] < t:
                entry.last[key] = t
        entry.read_at = now
        _cache[sid] = entry
        return dict(entry.last)
