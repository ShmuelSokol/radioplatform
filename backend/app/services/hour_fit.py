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
# Songs overlap by about this much at a change when a song has no measured
# crossfade point (queue.handover_seconds gives each song's own)
CROSSFADE = 3.0
# Ending a little after the hard item costs only the fade; ending before it relies
# on the early start and, a second or two earlier still, lets one more song start.
# Song changes drift by a few seconds, so a fit that ends early counts as worse:
# as bad as a song cut 40 s before its end, since a few seconds of a new song go
# out and are cut.
EARLY_COST = 40.0
# Song changes come a few seconds sooner than planned (live 2026-11-17 23:00Z: the song
# planned to end 1 s past the hour ended 1.9 s before it, and a new song was handed out
# and cut after 2.4 s). So "ends after the hard item" needs this much to spare.
SAFE_PAST = 5.0
# A song starting this soon is already announced to listeners' pages: not moved
FROZEN = 30.0
# Seconds between looks per station (the scheduler passes every second or two)
EVERY = 10.0
CONFIG_KEY = "fit_before_hard_items"  # automation_config: false switches it off

_last_look: dict[str, float] = {}
_said_nothing: dict[str, datetime] = {}


@dataclass
class _Slot:
    entry: QueueEntry
    start: datetime
    seconds: float


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# The stream server starts the next song this long before the one on air reaches its
# end point (radio.liq: cross(duration=3.))
ENGINE_CROSS = 3.0
# Songs whose end point is asked for per look (each is one short background job)
ANALYSE_PER_LOOK = 12


def _seconds(entry, engine: bool = False) -> float | None:
    """How long the entry holds the air: its length up to the point where it hands
    over to the next item (the same rule the players use), or None if unknown.

    On the stream server a song plays from its analysed start to its analysed end
    point (liq_cue_in / liq_cue_out: leading silence and the trailing fade are
    dropped) and the next one starts ENGINE_CROSS before that. Live 2026-11-18 02:00Z:
    a 523.4 s file held the air for 512.4 s; counted as 520.6 s, the fit planned it
    to run 8 s past the hour and it ended 0.2 s before."""
    asset = entry.asset
    value = getattr(asset, "duration", None) if asset is not None else None
    if not value or value <= 0:
        return None
    from app.api.v1.queue import handover_seconds

    if engine and entry.preempt_at is None:
        from app.models.asset import stored_analysis
        from app.models.queue_entry import resume_offset

        analysis = stored_analysis(getattr(asset, "metadata_extra", None)) or {}
        cue_in = analysis.get("cue_in_seconds")
        cue_in = float(cue_in) if isinstance(cue_in, (int, float)) and cue_in > 0 else 0.0
        cue_in = max(cue_in, resume_offset(entry))
        cue_out = analysis.get("cue_out_seconds")
        end = float(cue_out) if isinstance(cue_out, (int, float)) and 0 < cue_out <= value else float(value)
        return max(1.0, end - cue_in - ENGINE_CROSS)
    return max(1.0, float(value) - handover_seconds(entry))


# With less than this before the last song starts, the fit goes ahead on the figures
# it has instead of waiting for an analysis
WAIT_FOR_ANALYSIS = 90.0


def _analysis_on() -> bool:
    """Analysis jobs are switched off in tests; then the fit uses what is stored."""
    from app.services import upcoming_analysis

    return upcoming_analysis.ENABLED


def _measured(entry) -> bool:
    """Whether the song's end point is known (its audio analysis is current)."""
    from app.models.asset import needs_analysis

    return entry.asset is not None and not needs_analysis(entry.asset)


def _ask_for_analysis(entries, engine: bool) -> None:
    """Start the analysis of songs the fit is about to rely on. Background jobs; the
    next look (10 s later) reads their result."""
    from app.services import playout_engine, upcoming_analysis

    for entry in entries[:ANALYSE_PER_LOOK]:
        try:
            if engine:
                playout_engine._level_job(entry.asset)
            else:
                upcoming_analysis.request(entry.asset)
        except Exception:  # never let a look-ahead job stop the scheduler pass
            logger.debug("analysis request failed", exc_info=True)


def _cut(total: float, room: float) -> float | None:
    """Cost of filling `room` seconds with `total` seconds of songs: the seconds lost
    to the fade, or a little more than that for ending just before the hard item.
    None when they end early enough that one more song would start and be cut."""
    if total < room - EARLY_START:
        return None
    if total < room + SAFE_PAST:
        return EARLY_COST + (room + SAFE_PAST - total)
    return total - room


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
            # Analyses finish in their own sessions between looks: read them afresh
            .execution_options(populate_existing=True)
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

    from app.config import settings

    engine = settings.engine_drives(station.id)

    def secs(entry):
        return _seconds(entry, engine)

    at = now
    if playing is not None and playing.started_at is not None:
        length = secs(playing)
        if length is None:
            return False  # its end is unknown: nothing can be planned around it
        at = max(now, _aware(playing.started_at) + timedelta(seconds=length))
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
            at += timedelta(seconds=secs(due) or 30.0)
            slots.clear()  # songs before a timed item are not moved across it
            continue
        if not songs:
            return False
        entry = songs.pop(0)
        length = secs(entry)
        if length is None:
            return False
        slots.append(_Slot(entry, at, length))
        at += timedelta(seconds=length)
    if not slots:
        return False
    last = slots[-1]
    # A song's end point is only known once it is analysed, and the players analyse
    # a song just before it airs: 20 minutes ahead the fit would plan on file lengths
    # (the 02:00Z song had no analysis yet when it was chosen). Ask for the songs
    # that decide the fit, and wait for the next look unless time is short.
    if _analysis_on():
        unknown = [slot.entry for slot in slots if not _measured(slot.entry)]
        if unknown:
            _ask_for_analysis(unknown[::-1], engine)
            if (last.start - now).total_seconds() > WAIT_FOR_ANALYSIS:
                return False
    room = (hard_at - last.start).total_seconds()
    # Counted the same way as the candidates: a song that ends a few seconds before
    # the hard item is not "loses nothing". On the stream server a whole new song is
    # handed out and cut after those seconds (live 2026-11-18 00:00Z: the song on air
    # ended 6.6 s early, the fit saw "loses -6.6 s" and left the queue alone).
    loses = _cut(last.seconds, room)
    if loses is None:
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
    spare = [e for e in songs if movable(e) and e.block_id == block and secs(e) is not None]
    if _analysis_on():
        # A candidate whose end point is not measured yet is asked for and left out
        # of this look; it can be chosen ten seconds later
        _ask_for_analysis([e for e in spare if not _measured(e)], engine)
        spare = [e for e in spare if _measured(e)]

    # One song in the last place...
    best_cut, best = loses, None
    for cand in spare:
        cut = _cut(secs(cand), room)
        if cut is not None and cut < best_cut:
            best_cut, best = cut, (cand,)
    # ...or the last two places filled again (one long song, or a pair)
    before = slots[-2] if len(slots) > 1 else None
    if before is not None and movable(before.entry, before.start) and before.entry.block_id == block:
        room2 = (hard_at - before.start).total_seconds()
        pool = [before.entry, last.entry, *spare]
        for a in pool:
            cut = _cut(secs(a), room2)
            if cut is not None and cut < best_cut and a is not before.entry:
                best_cut, best = cut, (a, None)
            for b in pool:
                if b is a or (a is before.entry and b is last.entry):
                    continue
                if _artist(a) and _artist(a) == _artist(b):
                    continue
                cut = _cut(secs(a) + secs(b), room2)
                if cut is not None and cut < best_cut:
                    best_cut, best = cut, (a, b)
    if best is None or loses - best_cut < WORTH_IT:
        # Said once per hard item, so a live report can tell "looked and found nothing"
        # from "did not look"
        if _said_nothing.get(key) != hard_at:
            _said_nothing[key] = hard_at
            logger.info(
                "Station %s: no better order found before the %s hard item (the song on air then loses %.0f s; %d songs to choose from)",
                station.id, hard_at.strftime("%H:%M:%S"), loses, len(spare),
            )
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
