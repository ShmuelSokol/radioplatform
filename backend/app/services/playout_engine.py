"""
Playout engine link.

The playout engine (Liquidsoap on the stream server, `infra/streaming/radio.liq`) is the
only thing that plays audio for the engine-driven station. The backend stays the brain:

- the engine ASKS what plays next        -> `handle_next`
- the engine TELLS us what went on air   -> `handle_started`
- we send it commands (skip, flush the pre-loaded track, timed items) -> `engine_*`
- the scheduler calls `tick` every few seconds to hand over hard-timed items a little
  ahead of time and to keep the engine's pre-loaded track in line with the queue.

There is one clock: the engine's. Nothing here advances playback by track duration.
State that is only needed between engine calls lives in memory (single backend process).
"""

import hmac
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings
from app.models.asset import stored_analysis
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry, hard_timed, is_soft_timed, soft_timed

logger = logging.getLogger(__name__)

# Hand a hard-timed item to the engine this long before it airs, so it is downloaded and ready
TIMED_LEAD_SECONDS = 45.0
# Plays shorter than this are not logged (e.g. a song cut off as a timed item starts)
MIN_LOGGED_PLAY_SECONDS = 1.0
# Raise an alert if the engine has not contacted us for this long
ENGINE_SILENT_ALERT_SECONDS = 120.0
ALERT_REPEAT_SECONDS = 1800.0


@dataclass
class EngineState:
    # Entry handed to the engine as "next" that has not been reported started yet
    cued_entry_id: str | None = None
    # Hard-timed entries already handed to the engine's timed lane
    timed_sent: set[str] = field(default_factory=set)
    # A manual skip is in flight: the outgoing entry is logged as skipped
    skip_requested: bool = False
    # Soft-timed groups (by their time) whose hard_by limit already made us fade the song
    hard_by_done: set[str] = field(default_factory=set)
    # Monotonic time of the engine's last call to us
    last_contact: float = 0.0
    last_alert: float = 0.0
    on_emergency: bool = False


_states: dict[str, EngineState] = {}


def get_state(station_id) -> EngineState:
    return _states.setdefault(str(station_id), EngineState())


def reset_state(station_id=None) -> None:
    if station_id is None:
        _states.clear()
    else:
        _states.pop(str(station_id), None)


def token_ok(presented: str | None) -> bool:
    expected = settings.PLAYOUT_ENGINE_TOKEN
    return bool(expected) and bool(presented) and hmac.compare_digest(presented, expected)


# ── Commands to the engine ───────────────────────────────────────────────────


async def _command(path: str, payload: dict | None = None) -> bool:
    url = settings.PLAYOUT_ENGINE_URL.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(
                url, json=payload or {}, headers={"X-Playout-Token": settings.PLAYOUT_ENGINE_TOKEN}
            )
        if resp.status_code != 200:
            logger.warning("Playout engine %s -> HTTP %s", path, resp.status_code)
            return False
        return True
    except Exception as e:
        logger.warning("Playout engine %s failed: %s", path, e)
        return False


async def engine_skip(station_id, fade_seconds: float | None = None) -> bool:
    """Fade out the current track and start the next one now.

    `fade_seconds` None = the engine's own default (KBR_SKIP_FADE_SECONDS)."""
    state = get_state(station_id)
    state.skip_requested = True
    ok = await _command("/skip", None if fade_seconds is None else {"fade": fade_seconds})
    if not ok:
        state.skip_requested = False
    return ok


async def engine_flush(station_id) -> bool:
    """Drop the engine's pre-loaded next track so it asks again."""
    get_state(station_id).cued_entry_id = None
    return await _command("/flush")


async def engine_timed(uri: str, at_epoch: float, entry_id: str = "") -> bool:
    """Load an item now and put it on air at `at_epoch` (music fades under it).

    The engine ignores an `entry_id` it already holds, so handing the same item over twice
    (after a backend restart, when our own record of it is gone) does not air it twice.
    """
    return await _command("/timed", {"uri": uri, "at": at_epoch, "id": entry_id})


async def engine_overlay(asset) -> bool:
    """Play an asset on top of what is on air (Mix over). The item on air carries on."""
    url = audio_url(asset)
    if not url:
        return False
    fields = {"kbr_lane": "overlay", "title": asset.title or ""}
    analysis = stored_analysis(asset.metadata_extra)
    gain = analysis.get("replay_gain_db")
    if isinstance(gain, (int, float)) and gain != 0:
        fields["liq_amplify"] = f"{float(gain):.2f} dB"
    annotations = ",".join(f"{k}={_quote(v)}" for k, v in fields.items())
    return await _command("/overlay", {"uri": f"annotate:{annotations}:{url}"})


# ── Building what the engine plays ───────────────────────────────────────────


def audio_url(asset) -> str | None:
    if not asset or not asset.file_path:
        return None
    file_path = asset.file_path
    if file_path.startswith("http://") or file_path.startswith("https://"):
        return file_path
    if settings.supabase_storage_enabled:
        bucket = settings.SUPABASE_STORAGE_BUCKET
        return f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_path}"
    return None


def _quote(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    return f'"{text}"'


def build_uri(entry: QueueEntry, lane: str) -> str | None:
    """Liquidsoap `annotate:` URI carrying the entry id, titles, cue points and level."""
    asset = entry.asset
    url = audio_url(asset)
    if not url:
        return None
    analysis = (
        stored_analysis(asset.metadata_extra)
    )
    fields = {
        "kbr_entry": str(entry.id),
        "kbr_lane": lane,
        "title": asset.title or "",
        "artist": asset.artist or "",
    }
    cue_in = analysis.get("cue_in_seconds")
    cue_out = analysis.get("cue_out_seconds")
    if isinstance(cue_in, (int, float)) and cue_in > 0:
        fields["liq_cue_in"] = f"{float(cue_in):.3f}"
    if isinstance(cue_out, (int, float)) and cue_out > 0:
        fields["liq_cue_out"] = f"{float(cue_out):.3f}"
    gain = analysis.get("replay_gain_db")
    if isinstance(gain, (int, float)) and gain != 0:
        fields["liq_amplify"] = f"{float(gain):.2f} dB"
    annotations = ",".join(f"{k}={_quote(v)}" for k, v in fields.items())
    return f"annotate:{annotations}:{url}"


# ── Choosing the next entry (same rules as the time-based engine in queue.py) ──


async def pick_next(
    db: AsyncSession, station_id, exclude_ids: set[str] | None = None
) -> QueueEntry | None:
    now = datetime.now(UTC)
    exclude = {str(x) for x in (exclude_ids or set())}

    def usable(entries):
        for e in entries:
            if str(e.id) not in exclude:
                return e
        return None

    # 1. Clock-based ad slots whose time has arrived play after the current song
    ads = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            soft_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now,
        )
        # A block's items share one time and play in their order
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
        .limit(len(exclude) + 1)
    )
    entry = usable(ads.unique().scalars().all())
    if entry:
        return entry

    # 2. Otherwise the next entry by position (future-timed entries wait for their time)
    regular = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now),
        )
        .order_by(QueueEntry.position)
        .limit(len(exclude) + 1)
    )
    return usable(regular.unique().scalars().all())


# ── Calls from the engine ────────────────────────────────────────────────────


def _touch(state: EngineState) -> None:
    state.last_contact = time.monotonic()


async def handle_hello(station_id) -> None:
    """The engine (re)started: it holds nothing of ours any more."""
    reset_state(station_id)
    _touch(get_state(station_id))
    logger.info("Playout engine connected for station %s", station_id)


async def _reconcile_given(db: AsyncSession, station_id, given: list[str]) -> set[str]:
    """`given` is what the engine says it was handed, newest first.

    Every entry in it has gone to the engine, so none may be handed out again. The newest
    may still be waiting for its start report. An older one that we still show as pending
    has aired without its report reaching us (we were unreachable): close it.
    """
    ids: list[uuid.UUID] = []
    for raw in given[:10]:
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    if len(ids) > 1:
        res = await db.execute(
            select(QueueEntry).where(
                QueueEntry.id.in_(ids[1:]),
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
            )
        )
        lost = res.scalars().all()
        for entry in lost:
            logger.warning("Entry %s aired without a start report reaching us; closing it", entry.id)
            entry.status = "played"
        if lost:
            await db.commit()
    return {str(i) for i in ids}


async def handle_next(db: AsyncSession, station_id, given: list[str] | None = None) -> dict | None:
    """The engine wants its next track: {"uri", "entry_id"}, or None if the queue is empty."""
    state = get_state(station_id)
    _touch(state)
    already_given = await _reconcile_given(db, station_id, given or [])

    # The engine only asks when it holds nothing pre-loaded, so an entry we already handed
    # out is on its way to air (its "started" report may not have reached us yet).
    exclude = set(state.timed_sent) | already_given
    if state.cued_entry_id:
        exclude.add(state.cued_entry_id)

    # Looping: queue the next repeat of the item on air, unless one is already waiting
    try:
        from app.api.v1.queue import add_loop_repeat

        await add_loop_repeat(db, station_id, ignore_ids=exclude)
        await db.commit()
    except Exception as e:
        logger.warning("Loop repeat could not be queued: %s", e)

    for _ in range(5):
        entry = await pick_next(db, station_id, exclude)
        if entry is None:
            # Nothing queued: try to refill once, then give up until the engine asks again
            try:
                from app.api.v1.queue import _is_blacked_out, _replenish_queue

                if not await _is_blacked_out(db, station_id):
                    await _replenish_queue(db, station_id)
                entry = await pick_next(db, station_id, exclude)
            except Exception as e:
                logger.warning("Replenish on empty queue failed: %s", e)
            if entry is None:
                return None
        uri = build_uri(entry, "main")
        if uri:
            state.cued_entry_id = str(entry.id)
            logger.info("Engine next -> %s", entry.id)
            return {"uri": uri, "entry_id": str(entry.id)}
        # No playable file for this entry: drop it and try the following one
        logger.error("Queue entry %s has no playable audio URL; skipping it", entry.id)
        entry.status = "skipped"
        await db.commit()
    return None


async def handle_started(
    db: AsyncSession, station_id, entry_id: str | None, lane: str | None
) -> dict:
    """The engine reports what just went on air."""
    state = get_state(station_id)
    _touch(state)
    now = datetime.now(UTC)
    skipped = state.skip_requested
    state.skip_requested = False
    logger.info("Engine started -> entry=%s lane=%s", entry_id or "-", lane)

    entry: QueueEntry | None = None
    if entry_id:
        try:
            entry_uuid = uuid.UUID(str(entry_id))
        except ValueError:
            entry_uuid = None
        if entry_uuid:
            res = await db.execute(
                select(QueueEntry)
                .options(joinedload(QueueEntry.asset))
                .where(QueueEntry.id == entry_uuid, QueueEntry.station_id == station_id)
            )
            entry = res.unique().scalar_one_or_none()

    # Close whatever the database still shows as playing
    res = await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "playing"
        )
    )
    for prev in res.scalars().all():
        if entry is not None and prev.id == entry.id:
            continue
        started = prev.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started is not None and (now - started).total_seconds() >= MIN_LOGGED_PLAY_SECONDS:
            db.add(
                PlayLog(
                    id=uuid.uuid4(),
                    station_id=station_id,
                    asset_id=prev.asset_id,
                    start_utc=started,
                    end_utc=now,
                    source="manual" if skipped else "scheduler",
                )
            )
        prev.status = "skipped" if skipped else "played"

    if entry is None:
        await db.commit()
        if entry_id:
            # An entry we no longer have (removed from the queue after the engine loaded it)
            logger.warning("Engine started unknown queue entry %s", entry_id)
            return {"ok": True, "known": False}
        # No entry id at all: the engine is on its emergency playlist
        if not state.on_emergency:
            state.on_emergency = True
            await _alert(
                db,
                station_id,
                state,
                force=True,
                title="Playout engine is on its emergency playlist",
                message=(
                    "The engine could not get a track from the backend and is playing "
                    "its local emergency files."
                ),
            )
        return {"ok": True, "known": False}

    state.on_emergency = False
    if state.cued_entry_id == str(entry.id):
        state.cued_entry_id = None
    state.timed_sent.discard(str(entry.id))

    if entry.status != "playing":
        entry.status = "playing"
        entry.started_at = now

    # Keep positions compact: playing first, then pending in order
    compact = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.status.desc(), QueueEntry.position)
    )
    for idx, e in enumerate(compact.scalars().all()):
        e.position = idx + 1
    entry_pk = entry.id
    await db.commit()

    # Tell every client right away, then top the queue up
    try:
        from app.api.v1.queue import _broadcast_now_playing_entry, _is_blacked_out, _replenish_queue

        await _broadcast_now_playing_entry(db, station_id, entry_pk)
        if not await _is_blacked_out(db, station_id):
            await _replenish_queue(db, station_id)
    except Exception as e:
        logger.warning("Post-start broadcast/replenish failed (non-critical): %s", e)

    return {"ok": True, "known": True}


# ── Scheduler tick ───────────────────────────────────────────────────────────


async def _alert(
    db, station_id, state: EngineState, title: str, message: str, force: bool = False
) -> None:
    mono = time.monotonic()
    if not force and mono - state.last_alert < ALERT_REPEAT_SECONDS:
        return
    state.last_alert = mono
    try:
        from app.services.alert_service import create_alert

        await create_alert(
            db,
            alert_type="system",
            severity="critical",
            title=title,
            message=message,
            station_id=station_id,
        )
    except Exception as e:
        logger.warning("Could not create playout alert: %s", e)


async def _enforce_hard_by(db: AsyncSession, station_id, state: EngineState, now: datetime) -> None:
    """A soft-timed group waits for the item on air to end, but no later than its hard_by.

    Past that, the item on air fades and the group starts. We only fade once the engine has
    the group's first item pre-loaded (the queue-head check above makes it ask for it), so
    the group follows straight on.
    """
    res = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            soft_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now,
            QueueEntry.hard_by.isnot(None),
            QueueEntry.hard_by <= now,
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
        .limit(1)
    )
    head = res.scalars().first()
    if head is None:
        return
    group = head.preempt_at.replace(tzinfo=None).isoformat()
    if group in state.hard_by_done:
        return
    res = await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )
    current = res.scalars().first()
    # Part of the group (or another soft-timed item due before it) is already on air
    if current is not None and current.preempt_at is not None and is_soft_timed(current):
        return
    if state.cued_entry_id != str(head.id):
        return
    if await engine_skip(station_id):
        state.hard_by_done.add(group)
        if len(state.hard_by_done) > 200:
            state.hard_by_done.clear()
        logger.info("Soft-timed group at %s reached its limit: fading the item on air", group)


async def tick(db: AsyncSession, station) -> None:
    """Called by the scheduler loop for the engine-driven station."""
    station_id = station.id
    state = get_state(station_id)
    now = datetime.now(UTC)

    # During a live show the automation queue is left alone
    if (station.automation_config or {}).get("live_show_id"):
        return

    # 1. Hand hard-timed items (hourly ID, time, weather) to the engine ahead of time
    horizon = now + timedelta(seconds=TIMED_LEAD_SECONDS)
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            hard_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at > now,
            QueueEntry.preempt_at <= horizon,
        )
        # Handed over in play order: the engine plays a group (same time) in the order received
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )
    for entry in res.unique().scalars().all():
        key = str(entry.id)
        if key in state.timed_sent:
            continue
        # Silence entries mark Shabbos/Yom Tov blackouts; they are not "spots over music"
        uri = build_uri(entry, "timed")
        if not uri:
            continue
        at = entry.preempt_at
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if await engine_timed(uri, at.timestamp(), key):
            state.timed_sent.add(key)
            logger.info("Handed timed entry %s to the engine for %s", key, at.isoformat())

    # Forget timed entries that no longer exist as pending (played, removed)
    if state.timed_sent:
        res = await db.execute(
            select(QueueEntry.id).where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
            )
        )
        pending_ids = {str(x) for x in res.scalars().all()}
        state.timed_sent &= pending_ids

    # 2. Blackout ended while silence is on air: drop the remaining silence and move on
    try:
        from app.api.v1.queue import _is_blacked_out

        if not await _is_blacked_out(db, station_id):
            res = await db.execute(
                select(QueueEntry)
                .options(joinedload(QueueEntry.asset))
                .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            )
            current = res.unique().scalars().first()
            if current and current.asset and current.asset.asset_type == "silence":
                await db.execute(
                    update(QueueEntry)
                    .where(
                        QueueEntry.station_id == station_id,
                        QueueEntry.asset_id == current.asset_id,
                        QueueEntry.status == "pending",
                        or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now),
                    )
                    .values(status="played")
                )
                await db.commit()
                await engine_flush(station_id)
                await engine_skip(station_id)
                logger.info(
                    "Blackout ended: cleared silence on engine-driven station %s", station_id
                )
    except Exception as e:
        logger.warning("Blackout check in playout tick failed: %s", e)

    # 3. The queue changed after the engine pre-loaded its next track: make it ask again
    if state.cued_entry_id:
        desired = await pick_next(db, station_id, set(state.timed_sent))
        if desired is None or str(desired.id) != state.cued_entry_id:
            logger.info(
                "Queue head changed (%s -> %s): flushing the engine's pre-loaded track",
                state.cued_entry_id,
                desired.id if desired else None,
            )
            await engine_flush(station_id)

    # 4. "After the song, but no later than hard_by": the song is still on, so fade it
    await _enforce_hard_by(db, station_id, state, now)

    # 5. Watchdog
    if state.last_contact and time.monotonic() - state.last_contact > ENGINE_SILENT_ALERT_SECONDS:
        await _alert(
            db,
            station_id,
            state,
            title="Playout engine is not responding",
            message=(
                "No contact from the playout engine for over "
                f"{int(ENGINE_SILENT_ALERT_SECONDS)} seconds."
            ),
        )
