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
added, and only songs of a block that ends at the hard item but would air after it
are taken out (the next block's songs follow), so the queue can never run dry because of it, and the hard
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

from app.models.queue_entry import QueueEntry, hard_timed, is_soft_timed
from app.services import long_items

logger = logging.getLogger(__name__)

# How far ahead of a hard item the run-up is planned. The queue page shows times an
# hour ahead: planned only 20 minutes ahead, every fit moved songs the page had already
# shown, by 4 to 10 minutes after each break (simulator, Main 2026-11-19). Every hard
# item in the next 70 minutes is planned, in time order; nearer the time, a fit is
# only made again if the run-up has drifted (rarely, and then by one song).
HORIZON = timedelta(minutes=70)
# Queue entries looked at (the run-up itself and the songs that may replace it)
LOOK = 60
# A song that loses no more than this to the fade is left alone
ACCEPTABLE_CUT = 15.0
# Planned further ahead than REPLAN, a fit aims at this instead: song lengths drift by
# a few seconds before the break, and a run-up planned to lose 15 s lost 19 by then and
# was fitted again, which moved every song after the break that the queue page had
# already shown (simulator, Main 2026-11-19)
EARLY_TARGET = 8.0
REPLAN = timedelta(minutes=20)
# A swap must save at least this much, or the queue is left as it is
WORTH_IT = 20.0
# ...except one that brings the loss within ACCEPTABLE_CUT: that needs to save only
# this much (a song losing 19 s could never be saved 20 s; Main 15:28Z 2026-11-19)
WORTH_IT_INTO_ACCEPTABLE = 5.0
# A fit losing at most this much more than the best one (or within ACCEPTABLE_CUT) is
# as good: the one whose songs come from nearest in the queue is taken
NEAR_ENOUGH = 5.0
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
# The song the stream server has pre-loaded can still be exchanged (the next tick flushes
# it when the head of the queue changes), but not when it is about to start: the flush is
# refused in the last 45 s of the song on air and at most one happens per 15 s
# (playout_engine.KEEP_CUED_LAST_SECONDS, AUTO_FLUSH_MIN_INTERVAL)
PRELOADED_FROZEN = 75.0
CONFIG_KEY = "fit_before_hard_items"  # automation_config: false switches it off

_last_look: dict[str, float] = {}
_said_nothing: dict[str, tuple] = {}
# Hard items whose run-up has been planned once, per station. After that the run-up is
# only fitted again if it drifts past ACCEPTABLE_CUT: sponsor entries kept in step by the
# planner and measured end points move it by a few seconds, and fitting again at
# EARLY_TARGET each time reordered songs three or four times an hour, every time moving
# songs the queue page had shown (simulator, Main Friday 2026-11-27: songs shown at 7:48
# aired at 8:01)
_planned: dict[str, set[datetime]] = {}


@dataclass
class _Slot:
    entry: QueueEntry
    start: datetime
    seconds: float


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Used when the engine's own figure cannot be read (it never is 0 there: radio.liq
# always holds back a moment to make the change in)
ENGINE_MIN_CROSS = 0.2
# Songs whose end point is asked for per look (each is one short background job)
ANALYSE_PER_LOOK = 12


def _seconds(entry, engine: bool = False) -> float | None:
    """How long the entry holds the air: its length up to the point where it hands
    over to the next item (the same rule the players use), or None if unknown.

    On the stream server a song plays from its analysed start to its analysed end
    point (liq_cue_in / liq_cue_out: leading silence and the trailing fade are
    dropped) and the next one starts the item's own overlap before that: the very
    figures the engine is given with the song (playout_engine.build_uri: kbr_overlap,
    liq_cross_duration), so the two cannot drift apart again. Live 2026-11-18 02:00Z:
    a 523.4 s file held the air for 512.4 s; counted as 520.6 s, the fit planned it
    to run 8 s past the hour and it ended 0.2 s before. 04:00Z on Main: the engine
    had moved from a fixed 3 s overlap to the station's crossfade (1 s) while this
    still took off 3 s, so every song aired 2 s longer than counted and the last one
    started 9.5 s later than planned (said "loses 5 s", lost 17 s)."""
    asset = entry.asset
    value = getattr(asset, "duration", None) if asset is not None else None
    if not value or value <= 0:
        return None
    from app.api.v1.queue import handover_seconds

    if engine and entry.preempt_at is None:
        from app.models.asset import stored_analysis
        from app.models.queue_entry import resume_offset
        from app.services.playout_engine import _overlap

        analysis = stored_analysis(getattr(asset, "metadata_extra", None)) or {}
        cue_in = analysis.get("cue_in_seconds")
        cue_in = float(cue_in) if isinstance(cue_in, (int, float)) and cue_in > 0 else 0.0
        cue_in = max(cue_in, resume_offset(entry))
        cue_out = analysis.get("cue_out_seconds")
        end = float(cue_out) if isinstance(cue_out, (int, float)) and 0 < cue_out <= value else float(value)
        return max(1.0, end - cue_in - max(_overlap(entry), ENGINE_MIN_CROSS))
    return max(1.0, float(value) - handover_seconds(entry))


def on_air_until(playing, engine: bool):
    """When the item on air hands over, by the same length the queue is counted with.
    The engine's length runs from the cue-in (or a shiur's resume point), so it counts
    from the moment the item came on air; started_at lies that offset earlier
    (mark_started). The browser's length is from the top of the file."""
    from app.models.queue_entry import aired_from

    length = _seconds(playing, engine)
    if length is None or playing.started_at is None:
        return None
    start = aired_from(playing) if engine else playing.started_at
    return _aware(start) + timedelta(seconds=length)


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

    hard_times = (
        await db.execute(
            select(QueueEntry.preempt_at)
            .where(
                QueueEntry.station_id == station.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at > now,
                QueueEntry.preempt_at <= now + HORIZON,
                hard_timed(),
            )
            .distinct()
            .order_by(QueueEntry.preempt_at)
        )
    ).scalars().all()
    changed = False
    planned = _planned.setdefault(key, set())
    planned.intersection_update({_aware(t) for t in hard_times})
    for hard in sorted({_aware(t) for t in hard_times}):
        if await _fit_one(db, station, now, hard, key):
            changed = True
    return changed


async def _fit_one(db: AsyncSession, station, now: datetime, hard_at: datetime, key: str) -> bool:
    """The fit for one hard item: the songs before it, after any earlier timed items."""

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
    queue_order = list(songs)  # `songs` is used up below
    timed = list(
        (
            await db.execute(
                _read(
                    QueueEntry.status == "pending",
                    QueueEntry.preempt_at.isnot(None),
                    QueueEntry.preempt_at < hard_at,
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
        until = on_air_until(playing, engine)
        if until is None:
            return False  # its end is unknown: nothing can be planned around it
        at = max(now, until)
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
        # An earlier hard item falls inside it: a song is cut there (its own fit has
        # placed it); a shiur pauses or waits, which is planned when that item is next
        crossed = next(
            (e for e in timed if not is_soft_timed(e) and at < _aware(e.preempt_at) < at + timedelta(seconds=length)),
            None,
        )
        if crossed is not None:
            if entry.asset is not None and long_items.is_long_form(entry.asset):
                return False
            at = _aware(crossed.preempt_at)
            continue
        slots.append(_Slot(entry, at, length))
        at += timedelta(seconds=length)
    if not slots:
        return False
    last = slots[-1]
    # Songs drawn for a block that ends at this hard item but pushed past it (a fit
    # moved them, or songs ran longer than planned) would air in the next block: Main
    # 2026-11-23 aired a Carlebach song at 1:01 after its half hour ended at 1:00
    if await _drop_block_leftovers(db, station, now, last, songs, engine):
        return True
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
    planned = _planned.setdefault(key, set())
    first_plan = hard_at not in planned and hard_at - now > REPLAN
    target = EARLY_TARGET if first_plan else ACCEPTABLE_CUT
    if loses <= target:
        planned.add(hard_at)
        return False

    from app.services.playout_engine import cued_entry_id, held_entry_ids

    # The engine asks for its next song as soon as one starts, so during a long item the
    # song before the hard item is pre-loaded for many minutes (live 2026-11-18 04:00Z on
    # KBR Jerusalem: pre-loaded about 12 minutes ahead, never fitted, cut with 140 s left).
    # It is still exchangeable; only what sits in the engine's timed lane is not.
    preloaded = cued_entry_id(station.id) if engine else None
    held = held_entry_ids(station.id) - ({preloaded} if preloaded else set())

    def movable(slot_or_entry, start: datetime | None = None) -> bool:
        e = slot_or_entry
        if e.source != "auto" or e.preempt_at is not None or e.id in held:
            return False
        # Songs only: a station ID between songs stays where it was drawn, clear of the break
        if e.asset is not None and (e.asset.asset_type or "music").lower() != "music":
            return False
        if start is None:
            return True
        frozen = PRELOADED_FROZEN if e.id == preloaded else FROZEN
        return (start - now).total_seconds() >= frozen

    if not movable(last.entry, last.start):
        return False
    block = last.entry.block_id
    # Holiday songs stay where the fill spaced them (about every fourth song): pulled
    # forward into the last place they sat beside another holiday song (simulator,
    # Erev Chanukah 2026-11-19). One already in the last place may still move out.
    from app.models.holiday_music import HolidayItem

    holiday_ids = {str(a) for a in (await db.execute(select(HolidayItem.asset_id))).scalars().all()}
    spare = [e for e in songs if movable(e) and e.block_id == block and secs(e) is not None
             and str(e.asset_id) not in holiday_ids]
    if _analysis_on():
        # A candidate whose end point is not measured yet is asked for and left out
        # of this look; it can be chosen ten seconds later
        unmeasured = [e for e in spare if not _measured(e)]
        _ask_for_analysis(unmeasured, engine)
        spare = [e for e in spare if _measured(e)]
    else:
        unmeasured = []

    # Every way to fill the run-up again: (seconds lost, the songs that go there)
    options: list[tuple[float, tuple]] = []
    # One song in the last place...
    for cand in spare:
        cut = _cut(secs(cand), room)
        if cut is not None and cut < loses:
            options.append((cut, (cand,)))
    # ...or the last two places filled again (one long song, or a pair)
    before = slots[-2] if len(slots) > 1 else None
    if before is not None and movable(before.entry, before.start) and before.entry.block_id == block:
        room2 = (hard_at - before.start).total_seconds()
        pool = [before.entry, last.entry, *spare]
        for a in pool:
            cut = _cut(secs(a), room2)
            if cut is not None and cut < loses and a is not before.entry:
                options.append((cut, (a, None)))
            for b in pool:
                if b is a or (a is before.entry and b is last.entry):
                    continue
                if _artist(a) and _artist(a) == _artist(b):
                    continue
                cut = _cut(secs(a) + secs(b), room2)
                if cut is not None and cut < loses:
                    options.append((cut, (a, b)))
    best_cut, best = loses, None
    if options:
        least = min(cut for cut, _ in options)
        # Of the fits about as good as the best, the one taken from nearest in the
        # queue: the song pulled in airs earlier than the queue page showed and the
        # one it replaces later, by as far as they were apart (simulator, Main
        # 2026-11-19: a song shown at 00:57 aired at 02:13)
        good_enough = max(target, least + NEAR_ENOUGH)

        def reach(option) -> tuple:
            cut, pick = option
            return (max(e.position for e in pick if e is not None), cut)

        best_cut, best = min((o for o in options if o[0] <= good_enough), key=reach)
    saves = loses - best_cut if best is not None else 0.0
    worth = saves >= WORTH_IT or (best_cut <= ACCEPTABLE_CUT and saves >= WORTH_IT_INTO_ACCEPTABLE)
    if best is None or not worth:
        if loses <= ACCEPTABLE_CUT:
            planned.add(hard_at)  # good enough as it is: not reordered later for a few seconds
        # Said once per hard item, so a live report can tell "looked and found nothing"
        # from "did not look"
        # (and again when songs to choose from appear: the first look often has none
        # because their end points are still being measured)
        said = (hard_at, bool(spare))
        if _said_nothing.get(key) != said:
            _said_nothing[key] = said
            logger.info(
                "Station %s: queue left as it is before the %s hard item (the song on air then loses %.0f s; "
                "no swap among %d songs saves %.0f s or more; %d more songs not measured yet)",
                station.id, hard_at.strftime("%H:%M:%S"), loses, len(spare), WORTH_IT, len(unmeasured),
            )
        return False

    if len(best) == 1:
        _bring_forward(queue_order, last.entry, [best[0]])
    else:
        # One song runs to the hard item (the two it replaces follow it), or a pair
        _bring_forward(queue_order, before.entry, [e for e in best if e is not None])
    planned.add(hard_at)
    await db.flush()
    logger.info(
        "Station %s: songs before the %s hard item reordered (the song on air then loses %.0f s instead of %.0f s)",
        station.id, hard_at.strftime("%H:%M:%S"), best_cut, loses,
    )
    return True


async def _drop_block_leftovers(db: AsyncSession, station, now: datetime, last, after: list, engine: bool) -> bool:
    """Take out the automatic songs of the run-up's block that the queue now puts
    after the hard item, when the songs after them come from another block: the
    block ends there, so they would air in the wrong block. The next block's songs
    follow, so nothing runs dry. Returns True when something was taken out."""
    block = last.entry.block_id
    if not block or last.entry.source != "auto":
        return False
    leftovers: list[QueueEntry] = []
    ended = False
    for e in after:
        if e.source != "auto" or e.preempt_at is not None:
            break  # a person's pick: the queue after it is left as it is
        if e.asset is not None and (e.asset.asset_type or "music").lower() != "music":
            continue  # a station ID stays where it was drawn
        if e.block_id == block:
            leftovers.append(e)
            continue
        ended = True
        break
    if not ended or not leftovers:
        return False
    from app.services.playout_engine import cued_entry_id, held_entry_ids

    preloaded = cued_entry_id(station.id) if engine else None
    held = held_entry_ids(station.id) - ({preloaded} if preloaded else set())
    # Each one starts after the hard item at the earliest
    soon = (last.start - now).total_seconds()
    gone = [
        e for e in leftovers
        if e.id not in held and soon >= (PRELOADED_FROZEN if e.id == preloaded else FROZEN)
    ]
    if not gone:
        return False
    for e in gone:
        await db.delete(e)
    await db.flush()
    logger.info(
        "Station %s: %d song(s) of a block that ends at the %s hard item taken out of the queue (they would air after it)",
        station.id, len(gone), last.start.strftime("%H:%M:%S"),
    )
    return True


def _artist(entry) -> str | None:
    artist = getattr(entry.asset, "artist", None) if entry.asset is not None else None
    return artist.strip().lower() if artist else None


def _bring_forward(order: list[QueueEntry], first_place: QueueEntry, wanted: list[QueueEntry]) -> None:
    """Put `wanted` (in that order) at `first_place` and the places after it; the songs
    they pass each move one or two places on, keeping their order.

    Before, the song pushed out took the incoming one's old place: a song shown at
    00:29 went to where its replacement had been and aired at 02:37 (simulator, Main
    2026-11-19). Now it airs straight after the hard item, and the songs between
    move by a song's length at most."""
    start = order.index(first_place)
    end = max(order.index(e) for e in wanted)
    run = order[start:end + 1]
    positions = sorted(e.position for e in run)
    new = [*wanted, *(e for e in run if e not in wanted)]
    for entry, position in zip(new, positions):
        entry.position = position
