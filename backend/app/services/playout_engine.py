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
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings
from app.models.asset import Asset, stored_analysis
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry, hard_timed, is_soft_timed, mark_started, resume_offset, soft_timed

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
    # Asset of each entry handed to the engine (newest last), so a start report for an
    # entry deleted meanwhile (a schedule redraw) still shows what is on air
    handed: dict[str, str] = field(default_factory=dict)
    # "<entry on air>@<group time>" pairs whose long-item rule (pause/wait/cut) was applied
    long_done: set[str] = field(default_factory=set)
    # What the engine last said it was handed (its own `given` list): never handed again
    given: set[str] = field(default_factory=set)
    # Monotonic time of the last automatic flush of the pre-loaded track
    last_auto_flush: float = 0.0
    # Entry already reported as running past its end
    overrun_alerted: str | None = None
    # Entries the engine reported on air, newest last. Never handed out again, even
    # while their start report is still being written (it is not committed yet)
    recent_started: list[str] = field(default_factory=list)


HANDED_KEEP = 30


def held_entry_ids(station_id) -> set[uuid.UUID]:
    """Queue entries the engine holds (cued as next, or in its timed lane): they are on
    their way to air and must not be deleted or redrawn."""
    state = _states.get(str(station_id))
    if state is None:
        return set()
    ids = set(state.timed_sent)
    if state.cued_entry_id:
        ids.add(state.cued_entry_id)
    out = set()
    for raw in ids:
        try:
            out.add(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return out


def _note_handed(state: "EngineState", entry) -> None:
    state.handed[str(entry.id)] = str(entry.asset_id)
    while len(state.handed) > HANDED_KEEP:
        state.handed.pop(next(iter(state.handed)))


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


async def _command(path: str, payload: dict | None = None, *, station_id) -> bool:
    url = settings.engine_control_url(station_id) + path
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


async def engine_status(station_id) -> dict | None:
    """What the engine says is on air (its /status), or None when it does not answer."""
    url = settings.engine_control_url(station_id) + "/status"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(url, json={}, headers={"X-Playout-Token": settings.PLAYOUT_ENGINE_TOKEN})
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) and data.get("ok") else None
    except Exception:
        return None


async def engine_skip(station_id, fade_seconds: float | None = None) -> bool:
    """Fade out the current track and start the next one now.

    `fade_seconds` None = the engine's own default (KBR_SKIP_FADE_SECONDS)."""
    state = get_state(station_id)
    state.skip_requested = True
    ok = await _command("/skip", None if fade_seconds is None else {"fade": fade_seconds}, station_id=station_id)
    if not ok:
        state.skip_requested = False
    return ok


async def engine_flush(station_id) -> bool:
    """Drop the engine's pre-loaded next track so it asks again."""
    get_state(station_id).cued_entry_id = None
    return await _command("/flush", station_id=station_id)


async def engine_timed(station_id, uri: str, at_epoch: float, entry_id: str = "") -> bool:
    """Load an item now and put it on air at `at_epoch` (music fades under it).

    The engine ignores an `entry_id` it already holds, so handing the same item over twice
    (after a backend restart, when our own record of it is gone) does not air it twice.
    """
    return await _command("/timed", {"uri": uri, "at": at_epoch, "id": entry_id}, station_id=station_id)


async def engine_overlay(station_id, asset) -> bool:
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
    return await _command("/overlay", {"uri": f"annotate:{annotations}:{url}"}, station_id=station_id)


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
        # The engine keeps copies of music it played as its emergency fallback
        "kbr_kind": (asset.asset_type or "").lower(),
        "title": asset.title or "",
        "artist": asset.artist or "",
    }
    cue_in = analysis.get("cue_in_seconds")
    # A shiur paused by a timed item carries on where it stopped
    if resume_offset(entry) > 0:
        cue_in = max(float(cue_in or 0.0), resume_offset(entry))
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
    from app.api.v1.queue import OPERATOR_SOURCES
    from app.models.asset import not_placeholder

    now = datetime.now(UTC)
    exclude = {str(x) for x in (exclude_ids or set())}
    # Same rule as the browser-player path: placeholders and do-not-play items queued by
    # an automatic path are never picked (an operator's own pick still is). Read-only on
    # purpose: this runs inside the scheduler's long pass, and an UPDATE there held row
    # locks that /next then waited on.
    may_air = or_(
        QueueEntry.source.in_(OPERATOR_SOURCES),
        QueueEntry.asset_id.notin_(select(Asset.id).where(~not_placeholder())),
    )

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
            may_air,
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
            may_air,
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
    state.given = set(already_given)

    # The engine only asks when it holds nothing pre-loaded, so an entry we already handed
    # out is on its way to air (its "started" report may not have reached us yet).
    exclude = set(state.timed_sent) | already_given | set(state.recent_started)
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
            _note_handed(state, entry)
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
    if entry_id:
        state.recent_started = (state.recent_started + [str(entry_id)])[-10:]

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

    if entry is None and entry_id and str(entry_id) in state.handed:
        # Handed to the engine, then deleted here (a schedule redraw replaced the
        # unplayed songs): it is on air all the same. Without this, Main showed
        # nothing playing (listeners saw "Offline") while the engine played on.
        try:
            asset_uuid = uuid.UUID(state.handed[str(entry_id)])
        except ValueError:
            asset_uuid = None
        if asset_uuid is not None and await db.get(Asset, asset_uuid) is not None:
            entry = QueueEntry(
                id=uuid.uuid4(), station_id=station_id, asset_id=asset_uuid,
                position=0, status="pending", source="auto",
            )
            db.add(entry)
            await db.flush()
            await db.refresh(entry, ["asset"])
            logger.warning("Engine started entry %s that was deleted here; on air as %s", entry_id, entry.id)

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
        mark_started(entry, now)

    # Keep positions compact: playing first, then pending in order. One statement: row by
    # row it was ~690 UPDATEs on Main and made every start report take over 3 s.
    await db.flush()
    ranked = (
        select(
            QueueEntry.id.label("id"),
            func.row_number()
            .over(order_by=(QueueEntry.status.desc(), QueueEntry.position))
            .label("rn"),
        )
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .subquery()
    )
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.id == ranked.c.id, QueueEntry.position != ranked.c.rn)
        .values(position=ranked.c.rn)
        .execution_options(synchronize_session=False)
    )
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


def _airs_until(entry) -> datetime | None:
    """When the engine stops this entry: its start plus its cue-out point (or length)."""
    if not entry.started_at or entry.asset is None:
        return None
    start = entry.started_at if entry.started_at.tzinfo else entry.started_at.replace(tzinfo=UTC)
    cue_out = stored_analysis(entry.asset.metadata_extra).get("cue_out_seconds")
    length = cue_out if isinstance(cue_out, (int, float)) and cue_out > 0 else entry.asset.duration
    if not length:
        return None
    return start + timedelta(seconds=float(length))


async def _soft_items_during_long(db: AsyncSession, station, now, horizon) -> None:
    """A soft group (:30 weather, sponsorships) coming up while a shiur has more than
    five minutes left becomes hard before it is handed over, so the long-item rule
    below pauses the shiur for it (same rule as the browser player, queue.py)."""
    from app.api.v1.queue import _soft_items_pause_long
    from app.services import long_items

    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station.id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    if current is None or not long_items.is_long_form(current.asset):
        return
    if await _soft_items_pause_long(
        db, station.id, current, long_items.policy(station), now, horizon=horizon
    ):
        await db.commit()


async def _apply_long_item_rule(db: AsyncSession, station, state: EngineState, now, horizon) -> None:
    """A shiur or story on air when the next hard timed group is about to be handed over:
    the station's rule (automation_config.timed_during_long_items) decides. "pause" queues
    the shiur to carry on from where the group stops it, "wait" makes the group wait for
    its end (soft), "cut" just cuts it. Same rule as the browser-player path in queue.py;
    the engine path had none, and a Daf Yomi aired as part of a block held the 21:00 time
    check until 21:09 (2026-11-16)."""
    from app.api.v1.queue import _defer_hard_until_end, _same_timed_group
    from app.models.queue_entry import resume_source
    from app.services import long_items

    station_id = station.id
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
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )
    upcoming = [e for e in res.unique().scalars().all() if str(e.id) not in state.timed_sent]
    if not upcoming:
        return
    timed = upcoming[0]
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    if current is None or not long_items.is_long_form(current.asset):
        return
    if _same_timed_group(current, timed):
        return
    at = timed.preempt_at if timed.preempt_at.tzinfo else timed.preempt_at.replace(tzinfo=UTC)
    key = f"{current.id}@{at.isoformat()}"
    if key in state.long_done:
        return
    state.long_done = {k for k in state.long_done if k.startswith(f"{current.id}@")}
    state.long_done.add(key)

    rule = long_items.policy(station)
    if rule == "wait" and current.preempt_at is not None and not is_soft_timed(current):
        # The shiur itself is in the engine's timed lane (part of an earlier hard block):
        # a soft group would go to the music lane, which only takes over after the
        # interrupted song comes back (34 s late in the harness). Keep the groups hard
        # and move them to the moment the shiur ends.
        end = _airs_until(current)
        if end is not None:
            await db.execute(
                update(QueueEntry)
                .where(
                    QueueEntry.station_id == station_id,
                    QueueEntry.status == "pending",
                    hard_timed(),
                    QueueEntry.preempt_at.isnot(None),
                    QueueEntry.preempt_at < end,
                    QueueEntry.preempt_at > now,
                )
                .values(preempt_at=end)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            logger.info("Station %s: timed groups wait for %s, now at %s", station_id, current.asset_id, end)
        return
    if rule == "wait":
        await _defer_hard_until_end(db, station_id, current)
        await db.commit()
        logger.info("Station %s: timed group at %s waits for %s to end", station_id, at, current.asset_id)
        return
    if rule != "pause":
        return
    resume_at = long_items.resume_point(current, at)
    if resume_at is None:
        return
    # The shiur carries on first thing in the music lane, which takes over again when
    # the timed group has played (the engine re-asks for its next track: queue head changed)
    head = (current.position or 0) + 1
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position >= head,
        )
        .values(position=QueueEntry.position + 1)
        .execution_options(synchronize_session=False)
    )
    db.add(QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
        position=head, status="pending", source=resume_source(resume_at),
    ))
    await db.commit()
    logger.info(
        "Station %s: %s pauses at %.0f s for the timed group at %s; it resumes after",
        station_id, current.asset_id, resume_at, at,
    )


# The engine downloads a whole file before it can play it. Its pre-loaded track is kept,
# even if the queue head changed, when the item on air ends sooner than this: a flush
# 8 s before a song's end left nothing ready and the emergency folder played for two
# minutes on Main (2026-11-17 02:57Z). Operator actions still flush at once.
KEEP_CUED_LAST_SECONDS = 45.0
# At most one automatic flush this often (each one cancels a download in progress)
AUTO_FLUSH_MIN_INTERVAL = 15.0


async def _flush_if_head_changed(db: AsyncSession, station_id, state: EngineState, now) -> None:
    # Same exclusions as handle_next: an entry the engine already holds is never the head
    # it should be given, or the two would disagree and flush on every tick
    exclude = set(state.timed_sent) | (state.given - {state.cued_entry_id})
    desired = await pick_next(db, station_id, exclude)
    if desired is not None and str(desired.id) == state.cued_entry_id:
        return
    mono = time.monotonic()
    if mono - state.last_auto_flush < AUTO_FLUSH_MIN_INTERVAL:
        return
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    end = _airs_until(current) if current is not None else None
    # A timed item that is due now (a :15 sponsorship, a block that waited for the song)
    # always takes the place of the pre-loaded song: it is short, and waiting would slip
    # it a whole song
    due_timed = (
        desired is not None
        and desired.preempt_at is not None
        and (desired.preempt_at if desired.preempt_at.tzinfo else desired.preempt_at.replace(tzinfo=UTC)) <= now
    )
    if not due_timed and end is not None and (end - now).total_seconds() < KEEP_CUED_LAST_SECONDS:
        return
    state.last_auto_flush = mono
    logger.info(
        "Queue head changed (%s -> %s): flushing the engine's pre-loaded track",
        state.cued_entry_id,
        desired.id if desired else None,
    )
    await engine_flush(station_id)


# Alert when the item on air is still "on air" this long after its known end
OVERRUN_ALERT_SECONDS = 30.0


async def _check_overrun(db: AsyncSession, station_id, state: EngineState, now) -> None:
    """Main stalled at the end of a song for two minutes with no alert (2026-11-17 02:57Z):
    the engine never reported the next start. Say so within a minute."""
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    end = _airs_until(current) if current is not None else None
    if end is None or (now - end).total_seconds() < OVERRUN_ALERT_SECONDS:
        return
    if state.overrun_alerted == str(current.id):
        return
    state.overrun_alerted = str(current.id)
    title = (current.asset.title if current.asset else None) or "The item on air"
    await _alert(
        db,
        station_id,
        state,
        force=True,
        title="Playout engine did not move on",
        message=(
            f"\"{title}\" should have ended at {end:%H:%M:%S} UTC, but the engine has not "
            "reported the next item. The file may be longer than its stored length, or the "
            "engine may be stuck: press Next, and check the engine's log."
        ),
    )


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
    try:
        await _soft_items_during_long(db, station, now, horizon)
        await _apply_long_item_rule(db, station, state, now, horizon)
    except Exception as e:
        logger.warning("Long-item rule for station %s failed: %s", station_id, e)
        # A database error would otherwise fail the rest of the tick, including
        # handing the time check to the engine.
        await db.rollback()
        await db.refresh(station)
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
        if await engine_timed(station_id, uri, at.timestamp(), key):
            state.timed_sent.add(key)
            _note_handed(state, entry)
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
        await _flush_if_head_changed(db, station_id, state, now)

    # 4. "After the song, but no later than hard_by": the song is still on, so fade it
    await _enforce_hard_by(db, station_id, state, now)

    # 5. Watchdog: the item on air runs well past its end and nothing new was reported
    await _check_overrun(db, station_id, state, now)
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
