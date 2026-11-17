"""Fit the songs before a hard timed item to the time that is left.

A hard item (the time check on the hour) fades whatever is on air. The fill did
not look at the clock: on KBR Jerusalem a 5-minute song started at 20:58:59 and
the 21:00 time check cut it after 61 s (2026-11-17). Professional automation
back-times the run-up to the hour instead.

This does the same without touching how the next item is picked: shortly before
a hard item it works out which queued song would be on air at that moment, and
if most of it would be lost it swaps queue positions so that a song (or a pair
of songs) that ends close to the hour takes its place. Only the station's own
automatic songs are moved, only within the same schedule block, and never one
that is about to start or that the playout engine already holds. Nothing is
added or removed, so the queue can never run dry because of it, and the hard
item still starts on its second. Both players pick by position, so the browser
player and the engine follow the new order alike.

See wiki/hourly-slots.md ("Songs fitted to the time before the hour").
"""
import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.queue_entry import QueueEntry, hard_timed

logger = logging.getLogger(__name__)

# How far ahead of a hard item the run-up is planned
HORIZON = timedelta(minutes=20)
# Queue entries looked at (the run-up itself and the songs that may replace it)
LOOK = 60
# A song that loses no more than this to the fade is left alone
ACCEPTABLE_CUT = 15.0
# A swap must save at least this much, or the queue is left as it is
WORTH_IT = 20.0
# A timed item due within this of a song's end starts at once (queue.TIMED_EARLY_START_SECONDS)
EARLY_START = 8.0
# Songs overlap by about this much at a change
CROSSFADE = 3.0
# A song starting this soon is already announced to listeners' pages: not moved
FROZEN = 30.0
# Seconds between looks per station (the scheduler passes every second or two)
EVERY = 10.0
CONFIG_KEY = "fit_before_hard_items"  # automation_config: false switches it off

_last_look: dict[str, float] = {}


@dataclass
class _Slot:
    entry: QueueEntry
    start: datetime
    seconds: float


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _seconds(entry) -> float | None:
    asset = entry.asset
    value = getattr(asset, "duration", None) if asset is not None else None
    return float(value) if value and value > 0 else None


def _cut(total: float, room: float) -> float | None:
    """Seconds lost to the fade when `total` seconds of songs fill `room` seconds,
    or None when they end early enough that one more song would start and be cut."""
    if total < room - EARLY_START:
        return None
    return max(0.0, total - room)


async def fit_before_hard_item(db: AsyncSession, station, now: datetime | None = None, *, force: bool = False) -> bool:
    """Reorder the automatic songs before the next hard timed item so the one on air
    at that moment loses as little as possible. Returns True when the queue changed
    (the caller commits)."""
    config = station.automation_config or {}
    if config.get(CONFIG_KEY) is False or config.get("hard_start", True) is False:
        return False
    key = str(station.id)
    if not force:
        tick = _time.monotonic()
        if tick - _last_look.get(key, 0.0) < EVERY:
            return False
        _last_look[key] = tick
    now = now or datetime.now(timezone.utc)

    hard = (
        await db.execute(
            select(QueueEntry.preempt_at)
            .where(
                QueueEntry.station_id == station.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at > now,
                QueueEntry.preempt_at <= now + HORIZON,
                hard_timed(),
            )
            .order_by(QueueEntry.preempt_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if hard is None:
        return False
    hard_at = _aware(hard)

    def _read(*where, limit):
        return (
            select(QueueEntry)
            .options(joinedload(QueueEntry.asset))
            .where(QueueEntry.station_id == station.id, *where)
            .order_by(QueueEntry.position)
            .limit(limit)
        )

    playing = (await db.execute(_read(QueueEntry.status == "playing", limit=1))).unique().scalars().first()
    songs = list(
        (await db.execute(_read(QueueEntry.status == "pending", QueueEntry.preempt_at.is_(None), limit=LOOK)))
        .unique().scalars().all()
    )
    timed = list(
        (
            await db.execute(
                _read(
                    QueueEntry.status == "pending",
                    QueueEntry.preempt_at.isnot(None),
                    QueueEntry.preempt_at < hard,
                    limit=LOOK,
                )
            )
        ).unique().scalars().all()
    )
    timed.sort(key=lambda e: (_aware(e.preempt_at), e.position))

    at = now
    if playing is not None and playing.started_at is not None:
        length = _seconds(playing)
        if length is None:
            return False  # its end is unknown: nothing can be planned around it
        at = max(now, _aware(playing.started_at) + timedelta(seconds=length - CROSSFADE))
    last_start = hard_at - timedelta(seconds=EARLY_START)
    if at >= last_start:
        return False  # the item on air runs to the hard item

    # Play the queue forward to the hard item, the way the next item is picked:
    # a timed item whose time has come first, otherwise by position
    slots: list[_Slot] = []
    while at < last_start:
        due = next((e for e in timed if _aware(e.preempt_at) <= at + timedelta(seconds=EARLY_START)), None)
        if due is not None:
            timed.remove(due)
            at += timedelta(seconds=_seconds(due) or 30.0)
            slots.clear()  # songs before a timed item are not moved across it
            continue
        if not songs:
            return False
        entry = songs.pop(0)
        length = _seconds(entry)
        if length is None:
            return False
        slots.append(_Slot(entry, at, length))
        at += timedelta(seconds=length - CROSSFADE)
    if not slots:
        return False
    last = slots[-1]
    room = (hard_at - last.start).total_seconds()
    loses = last.seconds - room
    if loses <= ACCEPTABLE_CUT:
        return False

    from app.services.playout_engine import held_entry_ids

    held = held_entry_ids(station.id)

    def movable(slot_or_entry, start: datetime | None = None) -> bool:
        e = slot_or_entry
        if e.source != "auto" or e.preempt_at is not None or e.id in held:
            return False
        return start is None or (start - now).total_seconds() >= FROZEN

    if not movable(last.entry, last.start):
        return False
    block = last.entry.block_id
    spare = [e for e in songs if movable(e) and e.block_id == block and _seconds(e) is not None]

    # One song in the last place...
    best_cut, best = loses, None
    for cand in spare:
        cut = _cut(_seconds(cand), room)
        if cut is not None and cut < best_cut:
            best_cut, best = cut, (cand,)
    # ...or the last two places filled again (one long song, or a pair)
    before = slots[-2] if len(slots) > 1 else None
    if before is not None and movable(before.entry, before.start) and before.entry.block_id == block:
        room2 = (hard_at - before.start).total_seconds()
        pool = [before.entry, last.entry, *spare]
        for a in pool:
            cut = _cut(_seconds(a), room2)
            if cut is not None and cut < best_cut and a is not before.entry:
                best_cut, best = cut, (a, None)
            for b in pool:
                if b is a or (a is before.entry and b is last.entry):
                    continue
                if _artist(a) and _artist(a) == _artist(b):
                    continue
                cut = _cut(_seconds(a) + _seconds(b) - CROSSFADE, room2)
                if cut is not None and cut < best_cut:
                    best_cut, best = cut, (a, b)
    if best is None or loses - best_cut < WORTH_IT:
        return False

    if len(best) == 1:
        _swap(last.entry, best[0])
    else:
        first, second = best
        if second is None:
            # One song runs to the hard item; the two it replaces follow it
            _swap(before.entry, first)
        else:
            _place([before.entry, last.entry], [first, second])
    await db.flush()
    logger.info(
        "Station %s: songs before the %s hard item reordered (the song on air then loses %.0f s instead of %.0f s)",
        station.id, hard_at.strftime("%H:%M:%S"), best_cut, loses,
    )
    return True


def _artist(entry) -> str | None:
    artist = getattr(entry.asset, "artist", None) if entry.asset is not None else None
    return artist.strip().lower() if artist else None


def _swap(a: QueueEntry, b: QueueEntry) -> None:
    if a is not b:
        a.position, b.position = b.position, a.position


def _place(places: list[QueueEntry], wanted: list[QueueEntry]) -> None:
    """Put `wanted` at the queue positions of `places`, in order; entries pushed
    out take the positions the incoming ones leave."""
    positions = [e.position for e in places]
    freed = [e.position for e in wanted if e not in places]
    for entry, position in zip(wanted, positions):
        entry.position = position
    for entry in places:
        if entry not in wanted:
            entry.position = freed.pop(0)
