"""The last word on a station ID, at the moment it would go on air.

The fill places IDs between songs by predicting when everything will air
(`StationIdSprinkler`, playlist_generator_service.py). A soft break (:15, :30, :45)
waits for the song on air to end, so at air time it can land just before an ID the
fill had put minutes away from it: the simulator (2026-11-19) heard a break and then
an ID 0.9 minutes later, spoken next to spoken. So both pick paths (the browser
player's `_pick_next_pending` and the engine's `pick_next`) ask here first, and an
automatically queued ID is passed over, for good, when

- the item before it (the one on air now) is not a programme item (a break, a time
  check, a sponsorship, another jingle...), or
- a timed item is due within the station's `clear_minutes` of it, or
- it is shorter than MIN_STATION_ID_SECONDS (1.5 s), or
- the last ID aired less than the station's `min_minutes` ago.

One passed over moves one song later (`postpone`) and is asked again after that song;
it is skipped only when it is too short or the next ordinary item is another ID. The
browser path does this at once; the engine's pick is read-only, so the IDs it passed
over are kept here and moved at the next start report (`take_passed`).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.asset import MIN_STATION_ID_SECONDS, STATION_ID_CATEGORY_PREFIX
from app.models.queue_entry import QueueEntry

# Queue sources a person chose: never second-guessed
_PERSON_SOURCES = ("manual", "request", "loop")

# Engine stations: entry ids passed over, per station, until the next start report
_passed: dict[str, set[str]] = {}


def is_auto_station_id(entry: QueueEntry) -> bool:
    asset = getattr(entry, "asset", None)
    category = (getattr(asset, "category", None) or "").lower()
    return (
        category.startswith(STATION_ID_CATEGORY_PREFIX)
        and entry.preempt_at is None
        and (entry.source or "auto") not in _PERSON_SOURCES
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def must_wait(
    db: AsyncSession, station_id, entry: QueueEntry, now: datetime, handed: QueueEntry | None = None
) -> bool:
    """True when this automatically queued station ID must not air now (see above).

    handed: the item the engine already holds and plays before this one, whose start report
    has not come yet (radio.liq asks for the next item while a short one is still loading).
    Main 2026-11-19 13:05Z: two IDs back to back after an engine restart, because the check
    looked at the song before the first ID instead of the first ID."""
    if not is_auto_station_id(entry):
        return False
    from app.models.station import Station
    from app.services.playlist_generator_service import NOT_BEFORE_AN_ID, id_settings_for, on_air_seconds

    if float(getattr(entry.asset, "duration", None) or 0.0) < MIN_STATION_ID_SECONDS:
        return True  # too short for the engine to load the next song behind it
    station = await db.get(Station, station_id)
    settings = id_settings_for(station.automation_config if station is not None else None)
    playing = (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset))
        # what is on air (or, when a skip already closed it, what aired last)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["playing", "played"]),
               QueueEntry.started_at.isnot(None), QueueEntry.started_at > now - timedelta(hours=3),
               QueueEntry.id != entry.id)
        .order_by(QueueEntry.started_at.desc())
        .limit(1)
    )).unique().scalar_one_or_none()
    start = now
    if playing is not None:
        before = playing.asset
        kind = (getattr(before, "asset_type", None) or "").lower()
        if playing.preempt_at is not None or kind in NOT_BEFORE_AN_ID or is_auto_station_id(playing):
            return True
        if playing.started_at is not None and before is not None:
            ends = _aware(playing.started_at) + timedelta(
                seconds=on_air_seconds(before.duration, before.metadata_extra)
            )
            start = max(start, ends)
    if handed is not None and handed.started_at is None and handed.id != entry.id:
        nxt = handed.asset
        kind = (getattr(nxt, "asset_type", None) or "").lower()
        if handed.preempt_at is not None or kind in NOT_BEFORE_AN_ID or is_auto_station_id(handed):
            return True
        if nxt is not None:
            start += timedelta(seconds=on_air_seconds(nxt.duration, nxt.metadata_extra))
    # Not sooner than the station's shortest gap after the last ID that aired: the fill
    # spaced them by predicted times, and a cut song or a moved break brings them closer
    # (simulator: 10.2 minutes apart).
    from app.models.asset import Asset

    last_id = (await db.execute(
        select(QueueEntry.started_at).join(Asset, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["playing", "played"]),
               QueueEntry.started_at.isnot(None), QueueEntry.id != entry.id,
               QueueEntry.started_at > start - timedelta(minutes=settings.min_minutes),
               func.lower(Asset.category).like(f"{STATION_ID_CATEGORY_PREFIX}%"))
        .order_by(QueueEntry.started_at.desc()).limit(1)
    )).scalar()
    if last_id is not None and start - _aware(last_id) < timedelta(minutes=settings.min_minutes):
        return True
    length = float(getattr(entry.asset, "duration", None) or 10.0)
    margin = timedelta(minutes=settings.clear_minutes)
    # Half a minute more than the setting ahead: the start is an estimate (the engine asks
    # for the next item before the one now starting is reported), and the simulator
    # found an ID 2 min 59.9 s before a :30 time check
    ahead = margin + timedelta(seconds=30)
    due = (await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at < start + timedelta(seconds=length) + ahead,
        ).limit(1)
    )).first()
    if due is not None:
        return True
    # ... and behind: a break that went on air less than clear_minutes before (a short
    # song between them is not enough; simulator: an ID 2.8 min after a soft break)
    recent = (await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status.in_(["playing", "played"]),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.started_at.isnot(None),
            QueueEntry.started_at > start - margin,
        ).limit(1)
    )).first()
    return recent is not None


def pass_over(station_id, entry_id) -> None:
    _passed.setdefault(str(station_id), set()).add(str(entry_id))


def passed(station_id) -> set[str]:
    return _passed.get(str(station_id), set())


async def postpone(db: AsyncSession, station_id, entry: QueueEntry) -> bool:
    """Move a waiting ID that may not air now one song later: it takes the place of the
    next ordinary waiting item, which moves up. False (the caller skips it) when it is
    too short to air at all, or the next ordinary item is another ID or there is none.
    Skipping them outright left Main 50 to 74 minutes without an ID (2026-11-19): the
    one after a :30 or :45 sponsorship was lost every time."""
    asset = getattr(entry, "asset", None)
    if float(getattr(asset, "duration", None) or 0.0) < MIN_STATION_ID_SECONDS:
        return False
    nxt = (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending",
               QueueEntry.preempt_at.is_(None), QueueEntry.id != entry.id,
               QueueEntry.position >= entry.position)
        .order_by(QueueEntry.position).limit(1)
        .execution_options(populate_existing=True)
    )).unique().scalar_one_or_none()
    if nxt is None or is_auto_station_id(nxt):
        return False
    entry.position, nxt.position = nxt.position, entry.position
    if entry.position == nxt.position:  # equal places: put the ID just after it
        entry.position = nxt.position + 1
    await db.flush()
    return True


async def take_passed(db: AsyncSession, station_id) -> int:
    """The IDs the engine's pick passed over (start report): each moves one song later
    (`postpone`), or is marked skipped when it cannot."""
    ids = _passed.pop(str(station_id), set())
    if not ids:
        return 0
    uuids = []
    for x in ids:
        try:
            uuids.append(uuid.UUID(x))
        except ValueError:
            pass
    if not uuids:
        return 0
    rows = (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.id.in_(uuids))
    )).unique().scalars().all()
    for entry in rows:
        if not await postpone(db, station_id, entry):
            entry.status = "skipped"
    await db.flush()
    return len(rows)


# Waiting IDs this close to air are left to the air-time check: the engine may have
# loaded the next one already, and the estimate is good enough only further out
_TIDY_FROM = timedelta(minutes=5)
# Further out the estimate drifts by more than the margins (every song that starts a
# little early or late shifts all of them against the fixed breaks), so IDs there are
# left alone until they come nearer: tidying the whole two days at every top-up wore
# Main's IDs away, 80 waiting down to 42 in 90 simulated minutes with 4 aired
_TIDY_UNTIL = timedelta(minutes=90)


async def drop_misplaced(db: AsyncSession, station_id, now: datetime | None = None) -> int:
    """Move later waiting automatic IDs that the queue's own timeline now
    puts where they would be passed over at air time: right after a break, sponsorship or other spoken
    item, or within `clear_minutes` of a timed item. The fill placed them clear, but
    sponsorships planned afterwards, cut songs and soft breaks move things, and the
    queue page then showed an ID right after TEST SPONSOR 14 (2026-11-19). Walks the
    queue the same way as the fill (`Clock`, on-air lengths). Does not commit."""
    from app.models.asset import Asset
    from app.models.queue_entry import is_soft_timed
    from app.models.station import Station
    from app.services.playlist_generator_service import (
        NOT_BEFORE_AN_ID,
        Clock,
        TimedItem,
        id_settings_for,
        on_air_seconds,
    )

    now = now or datetime.now(UTC)
    station = await db.get(Station, station_id)
    if station is None:
        return 0
    settings = id_settings_for(station.automation_config)
    rows = (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )).unique().scalars().all()

    start = now
    timed: list[TimedItem] = []
    waiting: list[tuple[QueueEntry, float]] = []
    for entry in rows:
        asset: Asset | None = entry.asset
        seconds = on_air_seconds(getattr(asset, "duration", None), getattr(asset, "metadata_extra", None)
                                 if isinstance(getattr(asset, "metadata_extra", None), dict) else None)
        if entry.status == "playing":
            if entry.started_at is not None:
                start = max(start, _aware(entry.started_at) + timedelta(seconds=seconds))
        elif entry.preempt_at is not None:
            timed.append(TimedItem(max(_aware(entry.preempt_at), now), seconds, not is_soft_timed(entry)))
        else:
            waiting.append((entry, seconds))

    clock = Clock(start, timed)
    margin = timedelta(minutes=settings.clear_minutes)
    furthest = timedelta(minutes=max(settings.max_minutes - settings.min_minutes, 3.0))

    def fits(seconds: float, previous_kind: str | None) -> bool:
        at = clock.start()
        if previous_kind is not None and previous_kind in NOT_BEFORE_AN_ID:
            return False
        before = clock.last_timed_end
        if before is not None and at - before < margin:
            return False
        return clock.clear_of_timed(at, seconds, margin)

    previous_kind: str | None = None
    # An ID that cannot stay where it is moves later, after the next song that leaves
    # room, so the station keeps its every-12-to-20-minutes rhythm (only removing them
    # left a 55-minute gap on Main, 2026-11-19). When no clear place comes before the
    # next ID or within max-min minutes it stays where it is, and the air-time check
    # (must_wait, postpone) settles it: removing them here wore Main's IDs away.
    carried: QueueEntry | None = None
    carried_seconds = 0.0
    carried_from: datetime | None = None
    passed: list[QueueEntry] = []
    changed = 0

    def leave_carried() -> None:
        nonlocal carried
        carried = None  # left in its place (its few seconds no longer count below)

    for entry, seconds in waiting:
        if carried is not None and passed:
            if clock.start() - carried_from > furthest:
                leave_carried()
            elif fits(carried_seconds, previous_kind):
                # it takes the place of the last song it passed; those songs move up one
                places = [carried.position] + [e.position for e in passed]
                for moved, place in zip(passed, places):
                    moved.position = place
                carried.position = places[-1]
                clock.play(carried_seconds)
                previous_kind = "jingle"
                carried = None
                changed += 1
        if is_auto_station_id(entry):
            if carried is not None:
                leave_carried()  # this one takes its turn
            at = clock.start()
            if _TIDY_FROM <= at - now <= _TIDY_UNTIL and not fits(seconds, previous_kind):
                carried, carried_seconds, carried_from, passed = entry, seconds, at, []
                continue  # lifted out: it takes no air time here
        clock.play(seconds)
        previous_kind = (getattr(entry.asset, "asset_type", None) or "").lower() or None
        if carried is not None:
            passed.append(entry)
    if carried is not None:
        leave_carried()
    if changed:
        await db.flush()
    return changed
