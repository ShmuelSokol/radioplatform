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

import asyncio
import hmac
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings
from app.models.asset import Asset, stored_analysis
from app.models.engine_handoff import EngineHandoff
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry, hard_timed, is_soft_timed, mark_started, resume_offset, soft_timed, start_offset

logger = logging.getLogger(__name__)

# Hand a hard-timed item to the engine this long before it airs, so it is downloaded and ready
TIMED_LEAD_SECONDS = 45.0
# A hard item whose hand-over window was missed (backend restart, slow pass) still goes to
# the timed lane, and airs at once, while it is at most this late. Later than that it no
# longer cuts the item on air: it plays next in the music lane (pick_next step 1).
MISSED_HAND_OVER_SECONDS = 300.0
# Plays shorter than this are not logged (e.g. a song cut off as a timed item starts)
MIN_LOGGED_PLAY_SECONDS = 1.0
# A start report that carries its own time (a local FM box's kept report) is believed
# when it is this far in the past
STARTED_AT_MIN_LATE = 2.0
STARTED_AT_MAX_LATE = 7 * 86400.0
# An ordinary start report's own time is used when it is at most this old
STARTED_AT_LIVE_MAX_LATE = 60.0
# Raise an alert if the engine has not contacted us for this long
ENGINE_SILENT_ALERT_SECONDS = 120.0
ALERT_REPEAT_SECONDS = 1800.0
# Engine protocol (radio.liq sends `proto` with /hello and /next). From this version it
# takes a timed group in parallel (seq/of, reassembled in order) and /timed_drop. An
# older engine queues timed items in arrival order and has no /timed_drop, so it gets
# the serial hand-over and the plain flush + skip at blackout end.
ENGINE_PROTO_GROUPS = 2
# From this version the engine stays quiet for the last seconds before a hard timed item
# when it has no song (its hold lane); an older engine would play emergency music there.
ENGINE_PROTO_HOLD = 3
# A song that would start this close before a hard timed item is not handed out. The
# engine fills up to 3 s with quiet and starts the timed item early for more, as the
# browser player does (queue.TIMED_EARLY_START_SECONDS, the same 8 s).
HOLD_BEFORE_HARD_SECONDS = 8.0
# From this version the engine loads its next item while a host is on air (its live
# lane keeps it off the stream), so music is back the moment the show ends instead of
# after a download (about 5 s on Main, 2026-11-18). An older engine is handed nothing
# during a show.
ENGINE_PROTO_LIVE_PRELOAD = 4
# From this version the engine's live lane plays the show from /live.mp3, so the stream
# carries it (listener pages switch to the stream for a show; /playout-status says so)
ENGINE_PROTO_LIVE_AUDIO = 5


@dataclass
class EngineState:
    # Entry handed to the engine as "next" that has not been reported started yet
    cued_entry_id: str | None = None
    # When the engine was last handed its next track, and when an operator's change last
    # took one back (monotonic): Next waits a moment for a track handed over just now
    cued_at: float = 0.0
    operator_flush_at: float = 0.0
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
    # Counts the spells of emergency music, so a delayed alert knows its spell is over
    emergency_spell: int = 0
    # True once open emergency alerts were cleared after a start from the queue
    emergency_cleared: bool = False
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
    # Hard-timed entries already reported as never accepted by the engine
    timed_alerted: set[str] = field(default_factory=set)
    # Monotonic time of the first tick, the watchdog's reference until the engine calls
    watch_since: float = 0.0
    # Timed entries moved to follow an earlier group: the time of that group (epoch)
    follows: dict[str, float] = field(default_factory=dict)
    # Monotonic time old EngineHandoff rows were last pruned
    last_handoff_prune: float = 0.0
    # Protocol version the engine last reported (0 = an engine that reports none)
    engine_proto: int = 0
    # What the engine was last told about a live show (None: not told since it or
    # we started, so it is told again)
    live_sent: bool | None = None
    # One start report at a time: two at once both closed the item before and wrote
    # its play log twice (Main 20:31Z, 2026-11-18: the same sponsorship counted twice)
    started_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # (entry, monotonic time) of the last start report, to drop an exact repeat
    last_started: tuple[str, float] | None = None
    live_tried: float = 0.0
    # Entry handed to the engine during a live show, to start when the show ends: the
    # tick's flush of a track pre-loaded before the show leaves this one alone
    cued_for_after_show: str | None = None
    # Soft timed items that came due during the show and for which the engine was
    # already asked to choose the after-show item again
    after_show_recued: set[str] = field(default_factory=set)
    # Entries handed to the engine during a live show, in order, and those of them a
    # flush then dropped there unplayed. The engine can hold more than one (it asked
    # twice within 80 ms at take-over, Jerusalem 21:14:41Z 2026-11-18), a flush empties
    # its whole queue, but it only forgets the newest in its `given` list: the other
    # came back as "given", was never handed out again and was closed as aired. That
    # was the second part of the story the show had paused.
    show_preloaded: list[str] = field(default_factory=list)
    flushed_unplayed: set[str] = field(default_factory=set)
    # The engine said hello and has put nothing on air since: the entry we still show as
    # playing is not on air any more (it was cut by the restart), so the hold rule must
    # not wait for its end
    fresh_start: bool = False
    # The long item cut by an engine restart has been queued to carry on (once per start)
    restart_resumed: bool = False
    # The engine this station listens to (its X-Playout-Instance, a random id each engine
    # start). None until one calls, and for an engine that sends none
    instance: str | None = None
    # Other engines that called with this station's id, refused (instance -> monotonic
    # time of the check), so their retries do not ask the real engine every 2 s
    refused_instances: dict[str, float] = field(default_factory=dict)


HANDED_KEEP = 30
# A refused engine is checked again after this long (the real one may have gone)
INSTANCE_RECHECK_SECONDS = 60.0
SECOND_ENGINE_ALERT_TITLE = "Two playout engines for one station"



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


def cued_entry_id(station_id) -> uuid.UUID | None:
    """The song the engine has pre-loaded as its next one (not yet on air), if any.
    Unlike an item in its timed lane it can still be exchanged: when the head of the
    queue changes, the next tick flushes it and the engine asks again."""
    state = _states.get(str(station_id))
    if state is None or not state.cued_entry_id:
        return None
    try:
        return uuid.UUID(str(state.cued_entry_id))
    except ValueError:
        return None


def _note_handed(state: "EngineState", entry) -> None:
    state.handed[str(entry.id)] = str(entry.asset_id)
    while len(state.handed) > HANDED_KEEP:
        state.handed.pop(next(iter(state.handed)))


# EngineHandoff rows are kept this long (a start report comes within one item's length)
HANDOFF_KEEP_HOURS = 6
HANDOFF_PRUNE_SECONDS = 3600.0


async def _record_handed(db: AsyncSession, station_id, handed: list[tuple[str, str]]) -> None:
    """Write (entry id, asset id) pairs to EngineHandoff, so a start report for an entry
    deleted meanwhile still finds its asset after a backend restart. Never fails the
    caller: the in-memory `handed` map still covers the no-restart case."""
    if not handed:
        return
    try:
        now = datetime.now(UTC)
        for entry_id, asset_id in handed:
            await db.merge(EngineHandoff(
                entry_id=uuid.UUID(entry_id), station_id=station_id,
                asset_id=uuid.UUID(asset_id), handed_at=now,
            ))
        await db.commit()
    except Exception as e:
        logger.warning("Could not record the engine hand-over: %s", e)
        await db.rollback()


async def _handed_asset(db: AsyncSession, state: "EngineState", station_id, entry_id: str) -> str | None:
    """Asset of an entry handed to the engine: from memory, else from EngineHandoff."""
    if entry_id in state.handed:
        return state.handed[entry_id]
    try:
        row = await db.get(EngineHandoff, uuid.UUID(entry_id))
    except Exception as e:
        logger.warning("Could not read the engine hand-over record: %s", e)
        await db.rollback()
        return None
    if row is None or str(row.station_id) != str(station_id):
        return None
    return str(row.asset_id)


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


# One connection to the engine, kept open between commands. A new client per command
# paid a TCP + TLS handshake to the engine server on every Next, Fade and timed
# hand-over before the command itself could leave.
_KEEPALIVE_SECONDS = 30.0  # below the engine's nginx keep-alive (75 s), so we close first
_shared_client: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    loop = asyncio.get_running_loop()
    held = _shared_client
    if (
        held is None
        or held[0] is not loop
        or type(held[1]) is not httpx.AsyncClient
        or getattr(held[1], "is_closed", False)
    ):
        _shared_client = held = (loop, httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=_KEEPALIVE_SECONDS),
        ))
    return held[1]


async def close_client() -> None:
    """Close the kept connection (backend shutdown)."""
    global _shared_client
    held, _shared_client = _shared_client, None
    if held is not None and held[0] is asyncio.get_running_loop():
        try:
            await held[1].aclose()
        except Exception:
            pass


async def _command(
    path: str, payload: dict | None = None, *, station_id, wait: float = 4.0, slow_is_ok: bool = False
) -> bool:
    """`slow_is_ok`: the engine got the command and is still working on it when `wait` runs
    out (a /timed answers only once the file is downloaded). That counts as taken."""
    from app.services import playout_tunnel

    if playout_tunnel.connected(station_id):
        # A local FM box: no inbound line, its relay fetches the command (playout_tunnel).
        # Same rules as by HTTPS: `wait` is how long the answer is waited for, and a
        # command the relay picked up whose answer is not in yet is "still loading".
        got = await playout_tunnel.send(station_id, path, payload or {}, timeout=wait)
        if got is not None and got[0] == 200:
            return True
        if got is not None and got[0] == playout_tunnel.STILL_WORKING:
            logger.warning("Playout engine %s by relay: no answer after %.0f s%s", path, wait,
                           " (still loading; taken)" if slow_is_ok else "")
            return slow_is_ok
        logger.warning("Playout engine %s by relay -> %s", path, "not fetched" if got is None else got[0])
        return False
    url = settings.engine_control_url(station_id) + path
    try:
        resp = await _client().post(
            url, json=payload or {}, headers={"X-Playout-Token": settings.PLAYOUT_ENGINE_TOKEN},
            timeout=wait,
        )
        if resp.status_code != 200:
            logger.warning("Playout engine %s -> HTTP %s", path, resp.status_code)
            return False
        return True
    except httpx.ReadTimeout:
        logger.warning("Playout engine %s: no answer after %.0f s%s", path, wait,
                       " (still loading; taken)" if slow_is_ok else "")
        return slow_is_ok
    except Exception as e:
        logger.warning("Playout engine %s failed: %s", path, e)
        return False


async def engine_status(station_id) -> dict | None:
    """What the engine says is on air (its /status), or None when it does not answer."""
    from app.services import playout_tunnel

    if playout_tunnel.connected(station_id):
        got = await playout_tunnel.send(station_id, "/status", {}, timeout=3.0)
        data = got[1] if got is not None and got[0] == 200 else None
        return data if isinstance(data, dict) and data.get("ok") else None
    url = settings.engine_control_url(station_id) + "/status"
    try:
        resp = await _client().post(
            url, json={}, headers={"X-Playout-Token": settings.PLAYOUT_ENGINE_TOKEN}, timeout=3.0,
        )
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


async def engine_live(station_id, on: bool) -> bool:
    """A live show starts or has ended: the stream goes quiet under the host and the
    item on air does not come back (it was queued again to carry on), or the music
    lane starts again. The engine ignores a repeat."""
    ok = await _command("/live", {"on": bool(on)}, station_id=station_id)
    state = get_state(station_id)
    if ok:
        state.live_sent = bool(on)
        state.live_tried = 0.0
    return ok


async def engine_flush(station_id) -> bool:
    """Drop the engine's pre-loaded next track so it asks again."""
    state = get_state(station_id)
    state.cued_entry_id = None
    # During a live show nothing of the music lane is on air: all it was handed is dropped
    state.flushed_unplayed.update(state.show_preloaded)
    state.show_preloaded = []
    return await _command("/flush", station_id=station_id)


async def engine_timed(
    station_id, uri: str, at_epoch: float, entry_id: str = "", *, seq: int = 0, of: int = 1,
    wait: float = 4.0, slow_is_ok: bool = False,
) -> bool:
    """Load an item now and put it on air at `at_epoch` (music fades under it).

    The engine ignores an `entry_id` it already holds, so handing the same item over twice
    (after a backend restart, when our own record of it is gone) does not air it twice.
    `seq` / `of`: its place in a group handed over in parallel (same time); the engine
    waits for the whole group and queues it in `seq` order.
    """
    payload = {"uri": uri, "at": at_epoch, "id": entry_id}
    if of > 1:
        payload.update(seq=seq, of=of)
    return await _command("/timed", payload, station_id=station_id, wait=wait, slow_is_ok=slow_is_ok)


async def engine_timed_drop(station_id, entry_ids: list[str]) -> bool:
    """Drop these entries from the engine's timed lane: any of them that is still waiting
    there is skipped the moment it would start (silence left over after a blackout)."""
    return await _command("/timed_drop", {"ids": list(entry_ids)}, station_id=station_id)


async def take_back_timed(station_id, entry_ids: list[str]) -> None:
    """These entries are soft again (they wait for a live show to end): forget that the
    timed lane has them and tell the engine to skip them there. They air from the queue."""
    state = get_state(station_id)
    for key in entry_ids:
        state.timed_sent.discard(key)
    await engine_timed_drop(station_id, list(entry_ids))


async def _soft_items_during_show(db: AsyncSession, station, state: EngineState, now: datetime) -> None:
    """Each pass while a live show has the station: sponsorships that come due go back
    to waiting (live_show_service.soft_items_wait_for_show), and the first soft item
    whose time has come becomes the item that follows the show. The engine pre-loaded
    that item when the show began (the song or shiur the show paused); left alone, a
    sponsorship due under the host waited behind the whole rest of a shiur."""
    from app.models.queue_entry import soft_timed
    from app.services.live_show_service import soft_items_wait_for_show

    waiting = await soft_items_wait_for_show(db, station.id, now)
    if waiting:
        await db.commit()
        await take_back_timed(station.id, waiting)
    due = [str(i) for i in (await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station.id, QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None), QueueEntry.preempt_at <= now, soft_timed(),
        ).order_by(QueueEntry.preempt_at, QueueEntry.position).limit(20)
    )).scalars().all()]
    if not due or state.cued_for_after_show in due or set(due) <= state.after_show_recued:
        return
    # Once for each item: one that may not air (a lapsed contract) is never handed
    # out, and the engine must not be flushed for it on every pass
    state.after_show_recued.update(due)
    state.cued_for_after_show = None


async def close_timed_lost_to_live_show(db: AsyncSession, station_id, now: datetime) -> list[str]:
    """A live show has ended on an engine station. Hard items whose time passed while the
    host was on air never aired (the stream was the host's) and no start report will come.
    Left "pending" they did not just sit there: the engine still held them in its timed
    lane and played them with the next hard group, a second time check at the next top of
    the hour (review runs 2026-11-18, V3-52). All of them are closed here, handed over or
    not (what we handed over is forgotten by a backend restart), and the engine is told to
    drop them. Returns their ids."""
    state = get_state(station_id)
    rows = (await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending", QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= now, hard_timed(),
        )
    )).scalars().all()
    lost = []
    for entry in rows:
        entry.status = "skipped"
        lost.append(str(entry.id))
    if lost:
        await db.flush()
        for key in lost:
            state.timed_sent.discard(key)
        logger.info("Station %s: %d timed item(s) came due during the live show and are closed: %s",
                    station_id, len(lost), ", ".join(lost))
        await engine_timed_drop(station_id, lost)
    return lost


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


def _overlap(entry) -> float:
    from app.api.v1.queue import overlap_seconds

    # The engine decides by itself what a song does before a spoken item (radio.liq)
    return overlap_seconds(entry, speech_next=False)


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
        # ... under one name per item: the download's own name (liq-processXXXX.mp3) is
        # new each time, so a song played twice took two of its 40 places
        "kbr_asset": str(asset.id),
        # How long this item overlaps the next at its end: the station's song
        # crossfade, 0 for anything spoken (queue.overlap_seconds)
        "kbr_overlap": f"{_overlap(entry):.2f}",
        # How early the engine prepares the change. Never 0: with nothing held back
        # the engine has no moment to make the change in; 0.2 s plays out in full
        # when the overlap is 0.
        "liq_cross_duration": f"{max(_overlap(entry), 0.2):.2f}",
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


# ── Every item's own level before it is handed over ─────────────────────────
# Items were analysed when the item before them went on air, while the engine asked
# for them at that same moment (it pre-loads one track), so an item with no analysis
# was handed over without its level and aired as loud or quiet as its file (Jerusalem
# 2026-11-17 20:00Z: -19.9 to -10.6 LUFS). Now the item is analysed before it is
# handed over (at most LEVEL_WAIT_SECONDS), and items coming up are analysed ahead.
LEVEL_WAIT_SECONDS = 6.0
LOOK_AHEAD_ITEMS = 3
LOOK_AHEAD_TIMED = timedelta(minutes=15)
_level_jobs: dict[str, asyncio.Task] = {}


async def _analyse_alone(asset_id: str) -> None:
    from app.db.session import async_session_factory
    from app.services.audio_analysis_service import analyze_audio

    async with async_session_factory() as own:
        await analyze_audio(own, asset_id)
        await own.commit()


def _level_job(asset) -> asyncio.Task | None:
    """The analysis of this item: started now if it needs one, at most once per process."""
    from app.models.asset import needs_analysis
    from app.services import upcoming_analysis

    if not upcoming_analysis.ENABLED:  # off in tests
        return None
    if asset is None or not asset.file_path or (asset.asset_type or "").lower() == "silence":
        return None
    key = str(asset.id)
    job = _level_jobs.get(key)
    if job is None:
        if not needs_analysis(asset):
            return None
        if len(_level_jobs) > 2000:
            for k in [k for k, t in _level_jobs.items() if t.done()][:1000]:
                _level_jobs.pop(k, None)
        job = asyncio.get_running_loop().create_task(_analyse_alone(key))
        _level_jobs[key] = job
    return job


async def ensure_level(db: AsyncSession, entry: QueueEntry, wait: float = LEVEL_WAIT_SECONDS) -> None:
    """Give `entry`'s item its analysis (level, cue points) before it is handed over.
    Waits at most `wait` seconds; a slower analysis carries on and serves the next airing."""
    job = _level_job(entry.asset)
    if job is None:
        return
    if not job.done():
        try:
            await asyncio.wait_for(asyncio.shield(job), wait)
        except asyncio.TimeoutError:
            logger.warning("Analysis of %s not ready in %.0f s; handed over without its level",
                           entry.asset_id, wait)
            return
        except Exception:
            pass
    if job.done() and not job.cancelled() and job.exception() is None:
        await db.refresh(entry.asset, ["metadata_extra"])


async def _analyse_ahead(db: AsyncSession, station_id, now: datetime) -> None:
    """Start the analysis of the next few items and of timed items due soon."""
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now + LOOK_AHEAD_TIMED),
        )
        .order_by(QueueEntry.preempt_at.isnot(None).desc(), QueueEntry.preempt_at, QueueEntry.position)
        .limit(LOOK_AHEAD_ITEMS + 10)
    )
    entries = res.unique().scalars().all()
    timed = [e for e in entries if e.preempt_at is not None]
    music = [e for e in entries if e.preempt_at is None][:LOOK_AHEAD_ITEMS]
    for e in timed + music:
        _level_job(e.asset)


# ── Choosing the next entry (same rules as the time-based engine in queue.py) ──


async def pick_next(
    db: AsyncSession, station_id, exclude_ids: set[str] | None = None
) -> QueueEntry | None:
    from app.api.v1.queue import OPERATOR_SOURCES
    from app.models.asset import not_placeholder
    from app.services import station_id_guard as id_guard
    from app.services.station_blocks import blocked_asset_ids

    now = datetime.now(UTC)
    exclude = {str(x) for x in (exclude_ids or set())}
    # Same rule as the browser-player path: placeholders and do-not-play items queued by
    # an automatic path are never picked (an operator's own pick still is). Read-only on
    # purpose: this runs inside the scheduler's long pass, and an UPDATE there held row
    # locks that /next then waited on.
    may_air = or_(
        QueueEntry.source.in_(OPERATOR_SOURCES),
        and_(
            QueueEntry.asset_id.notin_(select(Asset.id).where(~not_placeholder())),
            # nor an item this station alone was told not to play
            QueueEntry.asset_id.notin_(blocked_asset_ids(station_id)),
        ),
    )
    # Nor a sponsorship whose contract no longer covers today (it waited past its
    # last day behind a long item). Read-only here too; the queue refill marks them.
    from app.models.station import Station
    from app.services.spot_schedule_service import lapsed_contract_assets

    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    lapsed = await lapsed_contract_assets(db, station, now) if station is not None else set()
    if lapsed:
        may_air = and_(
            may_air,
            or_(
                QueueEntry.source.in_(OPERATOR_SOURCES),
                QueueEntry.asset_id.notin_([uuid.UUID(x) for x in lapsed]),
            ),
        )

    def usable(entries):
        for e in entries:
            if str(e.id) not in exclude:
                return e
        return None

    # 1. Timed items whose time has arrived play first: soft ones (ad slots, blocks that
    # wait for the song) and hard ones the timed lane never got (their hand-over window
    # was missed). Same rule as the browser path (_pick_next_pending): by position, an
    # hourly time check created hours ahead aired only after every song queued before it.
    ads = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
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
        .limit(len(exclude) + len(id_guard.passed(station_id)) + 3)
    )
    # A station ID that would follow a break or another spoken item, or land next to a
    # break, is passed over for good (services/station_id_guard.py)
    for e in regular.unique().scalars().all():
        if str(e.id) in exclude or str(e.id) in id_guard.passed(station_id):
            continue
        if await id_guard.must_wait(db, station_id, e, now):
            id_guard.pass_over(station_id, e.id)
            continue
        return e
    return None


# ── Calls from the engine ────────────────────────────────────────────────────


def _touch(state: EngineState) -> None:
    state.last_contact = time.monotonic()


def _note_proto(state: EngineState, proto: int | None) -> None:
    state.engine_proto = proto if isinstance(proto, int) and proto > 0 else 0


async def instance_ok(station_id, instance: str | None) -> bool:
    """One engine per station. Two engines pulling one queue split it between them (each
    plays every other item), and two on the transmitter play over each other: a local FM
    box set up with a copied station id, or one left running after a move.

    Every engine sends a random id of its own (X-Playout-Instance, new at each start). The
    station's engine is the one the backend reaches on its control line (the stream
    server's URL, or a box's relay while it polls): a caller with another id while that
    engine answers with its own is refused and an alert raised. A restart is not refused,
    as the old engine is gone and the control line answers with the new id (or not yet at
    all). If the control line does not answer, the caller is taken: a network blip must
    not take a station off the air. An engine that sends no id is never checked."""
    if not instance:
        return True
    state = get_state(station_id)
    if state.instance == instance:
        return True
    mono = time.monotonic()
    checked = state.refused_instances.get(instance)
    if checked is not None and mono - checked < INSTANCE_RECHECK_SECONDS:
        return False
    status = await engine_status(station_id)
    reached = status.get("instance") if status else None
    if reached and reached != instance:
        first = checked is None
        state.refused_instances[instance] = mono
        logger.warning("Station %s: refused a second playout engine (%s); the station's engine is %s",
                       station_id, instance, reached)
        if first:
            task = asyncio.create_task(_second_engine_alert(station_id))
            _background.add(task)
            task.add_done_callback(_background.discard)
        return False
    if state.instance and state.instance != instance:
        logger.info("Station %s: playout engine %s replaces %s", station_id, instance, state.instance)
    state.instance = instance
    state.refused_instances.pop(instance, None)
    return True


async def _second_engine_alert(station_id) -> None:
    try:
        from app.db.engine import async_session_factory

        async with async_session_factory() as own:
            await _alert(
                own, station_id, get_state(station_id), SECOND_ENGINE_ALERT_TITLE,
                "A second playout engine called with this station's id and was refused, so it "
                "takes nothing from the queue. Look for a stream server or FM box set up with "
                "the wrong station, or one left running after a move.",
                force=True,
            )
            await own.commit()
    except Exception as e:
        logger.warning("Could not raise the second-engine alert: %s", e)


async def handle_hello(station_id, proto: int | None = None, instance: str | None = None) -> None:
    """The engine (re)started: it holds nothing of ours any more."""
    reset_state(station_id)
    state = get_state(station_id)
    state.instance = instance or None
    _note_proto(state, proto)
    _touch(state)
    state.fresh_start = True
    logger.info("Playout engine connected for station %s", station_id)


def _not_dropped_by_a_flush(state: "EngineState", given: list[str]) -> list[str]:
    """`given` without the entries a flush during a live show dropped in the engine
    unplayed: they are still ours to hand out. One that did start is known by its report."""
    state.flushed_unplayed -= set(state.recent_started)
    state.flushed_unplayed &= set(given)
    return [g for g in given if g not in state.flushed_unplayed]


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
                # (the newest can be in the list twice: handed out, dropped by a flush
                # during a live show, handed out again)
                QueueEntry.id.in_([i for i in ids[1:] if i != ids[0]]),
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


async def _resume_after_restart(db: AsyncSession, station_id) -> None:
    """The engine restarted (a deployment, a crash) while a shiur or story was on air: it
    carries on from where it was cut, first thing, like after a pause for the time
    check. It was lost with 48 minutes left (simulator, engine restart during the Daf
    Yomi, 2026-11-19)."""
    from app.models.queue_entry import RESUME_PREFIX, resume_source
    from app.services import long_items

    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    if current is None or current.started_at is None or not long_items.is_long_form(current.asset):
        return
    resume_at = long_items.resume_point(current, datetime.now(UTC))
    if resume_at is None:
        return
    # Already queued to carry on (a pause made a resume entry before the restart)
    waiting = await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "pending",
            QueueEntry.asset_id == current.asset_id, QueueEntry.source.like(f"{RESUME_PREFIX}%"),
        ).limit(1)
    )
    if waiting.first() is not None:
        return
    head = (current.position or 0) + 1
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending",
               QueueEntry.position >= head)
        .values(position=QueueEntry.position + 1)
        .execution_options(synchronize_session=False)
    )
    db.add(QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
        position=head, status="pending", source=resume_source(resume_at),
    ))
    await db.commit()
    logger.info("Station %s: engine restarted during %s; it carries on from %.0f s",
                station_id, current.asset_id, resume_at)


async def handle_next(
    db: AsyncSession, station_id, given: list[str] | None = None, proto: int | None = None,
    engine_live_on: bool = False,
) -> dict | None:
    """The engine wants its next track: {"uri", "entry_id"}, or None if the queue is empty."""
    state = get_state(station_id)
    _touch(state)
    # Sent with every ask, so a backend restart learns it again within one item
    if proto is not None:
        _note_proto(state, proto)
    given = _not_dropped_by_a_flush(state, list(given or []))
    already_given = await _reconcile_given(db, station_id, given)
    state.given = set(already_given)

    # During a live show the engine gets nothing from the queue: music would play on the
    # stream under the host, and the item the show paused (its resume entry, queued
    # first) would be used up. The engine retries by itself once the show is over.
    during_show = await _live_show_on(db, station_id)
    if during_show and not (engine_live_on and state.engine_proto >= ENGINE_PROTO_LIVE_PRELOAD):
        return None
    if engine_live_on and not during_show and _show_about_to_be_heard(station_id):
        # Told to go quiet a second before the host is heard; the take-over follows.
        # Telling it "the show is over" here brought the song back for 2 s where the
        # show should start (harness, 2026-11-18)
        return None
    if engine_live_on and not during_show:
        # The engine is still quiet for a show that is over (its end never reached it:
        # we restarted, or the command failed). Told in the background; it asks again.
        task = asyncio.create_task(engine_live(station_id, False))
        _background.add(task)
        task.add_done_callback(_background.discard)

    # (During a show nothing of ours is on air and the item starts when the show ends:
    # the hold rule has nothing to measure; the tick takes the item back if it would
    # then start just before a hard item.)
    if not during_show and state.engine_proto >= ENGINE_PROTO_HOLD and await _hold_before_hard_item(
        db, station_id, state.cued_entry_id or (given[0] if given else None),
        # Right after an engine restart the item we show as playing was cut: a song handed
        # now starts now. Waiting for that item's end left the engine 20 s on emergency
        # music when it ended just before a hard item (harness, engine crash 2026-11-18)
        skipping=state.skip_requested or state.fresh_start,
    ):
        return None

    if state.fresh_start and not state.restart_resumed and not during_show:
        state.restart_resumed = True
        try:
            await _resume_after_restart(db, station_id)
        except Exception as e:
            logger.warning("Station %s: resuming the item cut by the engine restart failed: %s", station_id, e)
            await db.rollback()

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

    unplayable = 0
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
                break
        await ensure_level(db, entry)
        if str(entry.id) in state.timed_sent:
            # A Next sent this very entry down the timed lane while we were choosing
            # (engine run 2026-11-18: it then went to both lanes, played in the timed
            # lane and made the time check behind it 20 s late)
            exclude.add(str(entry.id))
            continue
        uri = build_uri(entry, "main")
        if uri:
            entry_id, asset_id = str(entry.id), str(entry.asset_id)
            state.cued_entry_id = entry_id
            state.cued_at = time.monotonic()
            state.cued_for_after_show = entry_id if during_show else None
            state.flushed_unplayed.discard(entry_id)   # handed out again: the engine has it
            if during_show:
                state.show_preloaded.append(entry_id)
            _note_handed(state, entry)
            await _record_handed(db, station_id, [(entry_id, asset_id)])
            logger.info("Engine next -> %s", entry_id)
            # Its length, so the engine fetches the item after a short one (a 2 s station
            # ID) at once: asked only when the short one starts, the song's download
            # outlasted it and emergency music filled the gap (Jerusalem 03:46Z, 04:00Z
            # 2026-11-19)
            seconds = float(entry.asset.duration or 0.0) if entry.asset is not None else 0.0
            return {"uri": uri, "entry_id": entry_id, "seconds": round(seconds, 2)}
        # No playable file for this entry: drop it and try the following one
        logger.error("Queue entry %s has no playable audio URL; skipping it", entry.id)
        entry.status = "skipped"
        unplayable += 1
        await db.commit()
    if unplayable:
        # Every entry tried had no file the engine can fetch (relative paths with storage
        # not configured): the stream sits on its emergency files, so say so
        await _alert(
            db,
            station_id,
            state,
            title="Playout engine has nothing it can play",
            message=(
                f"{unplayable} queue item(s) had no audio URL the engine can fetch and were "
                "skipped; the engine is left without a track. Check the storage settings "
                "(SUPABASE_URL) and the items' files."
            ),
        )
    return None


async def _hold_before_hard_item(
    db: AsyncSession, station_id, cued: str | None = None, now: datetime | None = None,
    skipping: bool = False,
) -> bool:
    """True when the item on air ends within a few seconds before a hard timed item.

    A song handed out then starts and is cut after a second or two (Jerusalem 23:00Z
    2026-11-17: a 266 s song aired for 2.4 s before the time check). The engine is given
    nothing instead: the item on air plays to its end, the engine stays quiet for what
    is left (its hold lane) and the timed item starts on its second. The engine asks
    again every 2 s, so the next song is handed out as soon as the timed item is due.

    `cued`: the entry the engine was handed last. The engine asks the moment that entry
    starts, usually before its start report has arrived, so while it is still pending
    it counts as the item on air, starting where the one before it hands over.
    """
    now = now or datetime.now(UTC)
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    end = _airs_until(current) if current is not None else None
    from app.api.v1.queue import handover_seconds

    # Where the next song would start: the crossfade point of the item on air
    change = max(now, end - timedelta(seconds=handover_seconds(current))) if end is not None else now
    if skipping:
        # Next was pressed: the item on air ends now, whatever its length. Without this
        # the engine waiting to skip was handed nothing (the song on air "ends seconds
        # before the hard item") and Next put emergency music on air.
        change = now
        end = end or now
    held = None
    if cued and (current is None or str(current.id) != str(cued)):
        try:
            held = (
                await db.execute(
                    select(QueueEntry)
                    .options(joinedload(QueueEntry.asset))
                    .where(QueueEntry.id == uuid.UUID(str(cued)), QueueEntry.status == "pending")
                )
            ).unique().scalar_one_or_none()
        except ValueError:
            held = None
    if held is not None and held.asset is not None:
        cue_out = stored_analysis(held.asset.metadata_extra).get("cue_out_seconds")
        length = cue_out if isinstance(cue_out, (int, float)) and cue_out > 0 else held.asset.duration
        if not length:
            return False
        change = change + timedelta(seconds=max(0.0, float(length) - handover_seconds(held)))
    elif end is None:
        return False
    due = (
        await db.execute(
            select(QueueEntry.preempt_at)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                hard_timed(),
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > now,
                QueueEntry.preempt_at <= now + timedelta(minutes=30),
            )
            .order_by(QueueEntry.preempt_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if due is None:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    room = (due - change).total_seconds()
    hold = 0 <= room < HOLD_BEFORE_HARD_SECONDS
    if hold:
        logger.info(
            "No song for the engine now: it would start %.1f s before the hard item at %s (on air %s, pre-loaded %s)",
            room, due.strftime("%H:%M:%S"), getattr(current, "id", None), getattr(held, "id", None),
        )
    return hold


def _show_about_to_be_heard(station_id) -> bool:
    """The stream was quieted for a show whose delay is in its last second (on_soon)."""
    try:
        from app.api.v1.live_shows_ws import live_relay

        sid = str(station_id)
        air = live_relay.airs.get(sid)
        return sid in live_relay.quieted_early and air is not None and not air.closed and not air.finishing
    except Exception:
        return False


async def _live_show_on(db: AsyncSession, station_id) -> bool:
    from app.models.station import Station

    station = await db.get(Station, station_id)
    return bool(station is not None and (station.automation_config or {}).get("live_show_id"))


# A second report of the same entry this soon is the same start sent again
REPEAT_START_SECONDS = 10.0


async def handle_started(
    db: AsyncSession, station_id, entry_id: str | None, lane: str | None, at: float | None = None,
    quiet: bool = False, kept: bool = False,
) -> dict:
    """The engine reports what just went on air (see _handle_started). Reports for one
    station are taken one at a time, and an exact repeat (same entry within
    REPEAT_START_SECONDS) changes nothing."""
    state = get_state(station_id)
    async with state.started_lock:
        mono = time.monotonic()
        if (entry_id and not kept and state.last_started is not None
                and state.last_started[0] == str(entry_id)
                and mono - state.last_started[1] < REPEAT_START_SECONDS):
            logger.info("Engine started -> entry=%s again within %.1f s: repeat ignored",
                        entry_id, mono - state.last_started[1])
            _touch(state)
            return {"ok": True, "known": True, "repeat": True}
        result = await _handle_started(db, station_id, entry_id, lane, at=at, quiet=quiet, kept=kept)
        if entry_id:
            state.last_started = (str(entry_id), time.monotonic())
        return result


async def _handle_started(
    db: AsyncSession, station_id, entry_id: str | None, lane: str | None, at: float | None = None,
    quiet: bool = False, kept: bool = False,
) -> dict:
    """The engine reports what just went on air.

    `at` (epoch seconds) is when the engine put the item on air. `kept`: a report a
    local FM box's relay held while its line was down, so it can be days old. An
    ordinary report is believed up to STARTED_AT_LIVE_MAX_LATE: when the backend was
    slow to take it, the start was recorded when the report got through (4 s late with
    a 4 s backend: play log, site and proof of play all showed the time check late
    while it aired on time; harness under slow_backend, 2026-11-18).
    `quiet`: more kept reports follow at once; this one is recorded (queue, play log)
    without telling the pages, so they do not flick through an outage's items. Start times and the
    play log use it, so what aired offline is recorded when it aired."""
    state = get_state(station_id)
    _touch(state)
    now = datetime.now(UTC)
    if at is not None:
        try:
            late = now.timestamp() - float(at)
        except (TypeError, ValueError):
            late = 0.0
        # Only the past, and not older than the relay keeps anything (a wrong clock on
        # the box must not write nonsense)
        if (STARTED_AT_MIN_LATE <= late <= STARTED_AT_MAX_LATE) if kept else (0.0 <= late <= STARTED_AT_LIVE_MAX_LATE):
            now = datetime.fromtimestamp(float(at), UTC)
    skipped = state.skip_requested
    state.skip_requested = False
    state.fresh_start = False
    state.cued_for_after_show = None
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
    # Handed to the engine, then deleted here (a schedule redraw replaced the unplayed
    # songs): found before anything is changed, as the lookup may need a rollback
    handed_asset = (
        await _handed_asset(db, state, station_id, str(entry_id))
        if entry is None and entry_id else None
    )

    # Station IDs the pick passed over (station_id_guard) leave the queue
    from app.services.station_id_guard import take_passed

    await take_passed(db, station_id)

    # Close whatever the database still shows as playing (with its asset: the play log
    # row starts where it was heard, after its cue-in)
    res = await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset)).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "playing"
        )
    )
    for prev in res.unique().scalars().all():
        if entry is not None and prev.id == entry.id:
            continue
        started = prev.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started is not None:
            # A resumed item: the moment it came back on air, not the shifted start
            started = started + timedelta(seconds=start_offset(prev))
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

    if entry is None and handed_asset:
        # Handed to the engine, then deleted here: it is on air all the same. Without
        # this, Main showed nothing playing (listeners saw "Offline") while the engine
        # played on. EngineHandoff keeps this working across a backend restart.
        try:
            asset_uuid = uuid.UUID(handed_asset)
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
        # No entry id at all: the engine is on its emergency playlist. During a live
        # show that is expected (/next hands it nothing then), so no alert.
        if not state.on_emergency and not await _live_show_on(db, station_id):
            state.on_emergency = True
            state.emergency_spell += 1
            if EMERGENCY_ALERT_AFTER <= 0:
                await _emergency_alert(db, station_id, state)
            else:
                task = asyncio.create_task(_emergency_alert_later(station_id, state.emergency_spell))
                _background.add(task)
                task.add_done_callback(_background.discard)
        return {"ok": True, "known": False}

    if state.on_emergency or not state.emergency_cleared:
        # Back on the station's own queue: the emergency alert has served its purpose.
        # Also checked once per process, since a backend restart forgets on_emergency.
        state.emergency_cleared = True
        clear_emergency = True
    else:
        clear_emergency = False
    state.on_emergency = False
    if state.cued_entry_id == str(entry.id):
        state.cued_entry_id = None
    state.timed_sent.discard(str(entry.id))

    if entry.status != "playing":
        mark_started(entry, now)

    # The item on air goes just before the first waiting one (one row, not a renumber
    # of the whole queue: that took about 1 s of every start report)
    from app.services.queue_positions import put_playing_first

    await put_playing_first(db, station_id, entry)
    entry_pk = entry.id
    await db.commit()
    if clear_emergency:
        # In the background, in its own session, once this report's own changes are
        # committed: the alerts read took 0.87 s inside a start report on production
        # (2026-11-18 10:45Z), and the engine waits for the answer
        task = asyncio.create_task(_clear_emergency_alert(station_id))
        _background.add(task)
        task.add_done_callback(_background.discard)

    if quiet:
        return {"ok": True, "known": True}
    # Tell every client right away, then top the queue up
    try:
        from app.api.v1.queue import _broadcast_now_playing_entry, _is_blacked_out, _replenish_soon

        await _broadcast_now_playing_entry(db, station_id, entry_pk)
        if not await _is_blacked_out(db, station_id):
            # In the background, in its own session: the top-up is some 44 database
            # round trips (2 to 3 s from Railway), and the engine's start report waited
            # for it (15 of 36 reports took 3 to 9 s, 2026-11-18 07:47Z to 08:14Z)
            _replenish_soon(station_id)
    except Exception as e:
        logger.warning("Post-start broadcast/replenish failed (non-critical): %s", e)

    return {"ok": True, "known": True}


# ── Scheduler tick ───────────────────────────────────────────────────────────


EMERGENCY_ALERT_TITLE = "Playout engine is on its emergency playlist"


async def _clear_emergency_alert(station_id) -> None:
    """Resolve the open emergency-music alert: the engine is back on the station's queue."""
    try:
        from app.db.engine import async_session_factory
        from app.services.alert_service import clear_alerts

        async with async_session_factory() as own:
            if await clear_alerts(own, station_id, EMERGENCY_ALERT_TITLE,
                                  "the engine went back to the station's queue"):
                await own.commit()
    except Exception as e:
        logger.warning("Could not clear the emergency alert: %s", e)
# A critical alert (and its push) only when the emergency music lasts: a second of it
# while an item loads after a Next raised one on Main (03:46Z 2026-11-18) and cleared it
# half a second later.
EMERGENCY_ALERT_AFTER = 5.0


async def _emergency_alert(db, station_id, state: EngineState) -> None:
    await _alert(
        db,
        station_id,
        state,
        force=True,
        title=EMERGENCY_ALERT_TITLE,
        message=(
            "The engine could not get a track from the backend and is playing "
            "its local emergency files."
        ),
    )


async def _emergency_alert_later(station_id, spell: int) -> None:
    await asyncio.sleep(EMERGENCY_ALERT_AFTER)
    state = get_state(station_id)
    if not state.on_emergency or state.emergency_spell != spell:
        return  # back on the queue meanwhile
    try:
        from app.db.session import async_session_factory

        async with async_session_factory() as own:
            await _emergency_alert(own, station_id, state)
            # create_alert only flushes: without this the row was rolled back when the
            # session closed (seen in the log, gone from the alerts table, harness 2026-11-19)
            await own.commit()
    except Exception as e:
        logger.warning("Could not raise the emergency alert: %s", e)


async def _alert(
    db, station_id, state: EngineState, title: str, message: str, force: bool = False
) -> None:
    mono = time.monotonic()
    # last_alert 0 = never alerted. Compared as a time, it held back every alert for the
    # first 30 minutes after the host booted (time.monotonic() starts near 0 there).
    if not force and state.last_alert and mono - state.last_alert < ALERT_REPEAT_SECONDS:
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
        db, station.id, current, long_items.policy(station), now, horizon=horizon,
        include_due=False,
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
    upcoming = res.unique().scalars().all()
    if not upcoming:
        return
    timed = upcoming[0]
    # Already handed to the engine: what was on air then was not a shiur, but one may have
    # started since (a story that went on air 20 s before the hour was cut with 33 minutes
    # left and never resumed, simulator 2026-11-19). Only the pause applies from here on:
    # the engine holds the item at its time, so it can no longer wait or move.
    handed = str(timed.id) in state.timed_sent
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
    if handed and rule != "pause":
        return
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
    # The engine has already pre-loaded the next song for the music lane. Drop it so it
    # asks again and gets the resume. Done here unconditionally: the automatic swap only
    # runs while we remember what it pre-loaded, and a backend restart a minute before
    # the hour forgot that, so a song aired and the shiur came back 6 minutes late
    # (Jerusalem, 2026-11-17 14:00Z).
    if await engine_flush(station_id):
        state.last_auto_flush = time.monotonic()


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


async def take_operator_change(db: AsyncSession, station_id, *, before_next: bool = False) -> bool:
    """An operator changed the head of the queue (Play next, delete, reorder) or is
    pressing Next: if the engine holds a different song as its next, take it back now so
    it asks again and gets what the queue says. True if it was flushed.

    Without this, Play next followed by Next within a few seconds played the automatic
    song the engine already held and the operator's item one Next later: the automatic
    swap runs on the 3 s tick and at most once in 15 s (harness operator_queue_then_next,
    2026-11-18, 3 runs of 3).

    `before_next`: a Next follows at once, so the item on air ends now anyway and the
    last-seconds rule does not apply. Otherwise the pre-loaded song is kept when the item
    on air ends within KEEP_CUED_LAST_SECONDS (a flush then would leave nothing ready)."""
    state = get_state(station_id)
    if not state.cued_entry_id:
        return False
    exclude = set(state.timed_sent) | (state.given - {state.cued_entry_id})
    desired = await pick_next(db, station_id, exclude)
    if desired is not None and str(desired.id) == state.cued_entry_id:
        return False
    if desired is None and before_next:
        return False  # nothing else to give: keep what it holds rather than fall to emergency music
    if not before_next:
        res = await db.execute(
            select(QueueEntry)
            .options(joinedload(QueueEntry.asset))
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc().nullslast())
        )
        current = res.unique().scalars().first()
        end = _airs_until(current) if current is not None else None
        if end is not None and (end - datetime.now(UTC)).total_seconds() < KEEP_CUED_LAST_SECONDS:
            return False
    logger.info(
        "Operator changed the queue head (%s -> %s): flushing the engine's pre-loaded track",
        state.cued_entry_id, desired.id if desired else None,
    )
    state.last_auto_flush = state.operator_flush_at = time.monotonic()
    await engine_flush(station_id)
    return True


async def head_the_engine_does_not_hold(db: AsyncSession, station_id) -> QueueEntry | None:
    """The queue's next item, when the engine has pre-loaded a different one; else None.

    For Next. Taking the pre-loaded song back and skipping a moment later lost the item:
    on Main (03:46Z 2026-11-18) a sponsorship waiting behind the song was handed over,
    the skip came 0.8 s later while its file was still downloading, the engine's skip
    ran over it and it was closed as "aired without a start report". Next sends this
    item through the timed lane instead (loaded first, then on air), as Play now does."""
    state = get_state(station_id)
    if not state.cued_entry_id:
        return None
    exclude = set(state.timed_sent) | (state.given - {state.cued_entry_id})
    desired = await pick_next(db, station_id, exclude)
    if desired is None or str(desired.id) == state.cued_entry_id or desired.asset is None:
        return None
    # Short items only (a sponsorship, a jingle). Whatever plays in the timed lane is
    # followed at once by the next timed item handed over, so a long item there would
    # pull the hourly time check forward to its own end (engine run 2026-11-18: 37 s
    # early), and the pause-and-resume rules for long items do not apply in that lane.
    from app.services.long_items import is_long_form

    length = float(desired.asset.duration or 0)
    if not 0 < length <= NEXT_TIMED_LANE_MAX or is_long_form(desired.asset):
        return None
    if (desired.source or "").startswith("resume@"):
        return None
    until = datetime.now(UTC) + timedelta(seconds=length + NEXT_TIMED_LANE_CLEAR)
    hard_soon = await db.execute(
        select(QueueEntry.id).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "pending",
            QueueEntry.preempt_at.is_not(None), QueueEntry.preempt_at <= until, hard_timed(),
        ).limit(1)
    )
    if hard_soon.first() is not None:
        return None
    return desired


# How long Next waits for the engine to load the item it sends down the timed lane.
NEXT_LOAD_WAIT = 6.0
# Next uses the timed lane only for items this short, and only when no hard timed item is
# due until this long after the item would end.
NEXT_TIMED_LANE_MAX = 120.0
NEXT_TIMED_LANE_CLEAR = 60.0


async def next_goes_to(station_id, entry: QueueEntry) -> bool:
    """Next, when the engine holds a different song than the queue's head: the head is
    loaded and then put on air (the song on air plays until then), and the pre-loaded
    song is taken back so the engine asks for what follows. False if the engine did not
    take it (the caller falls back to an ordinary skip)."""
    uri = build_uri(entry, "timed")
    if not uri:
        return False
    state = get_state(station_id)
    entry_id = str(entry.id)
    if state.cued_entry_id == entry_id or entry_id in state.given:
        # The engine was handed this entry while we were choosing: it holds the right
        # item now, an ordinary skip plays it
        return False
    state.timed_sent.add(entry_id)
    state.skip_requested = True
    # The engine answers once the file is loaded. A big or slow file takes longer than the
    # answer is waited for: the engine has it all the same and airs it when it is loaded,
    # so that is not a refusal (a skip then would end the song with nothing ready).
    if not await engine_timed(station_id, uri, time.time(), entry_id, wait=NEXT_LOAD_WAIT, slow_is_ok=True):
        state.timed_sent.discard(entry_id)
        state.skip_requested = False
        return False
    logger.info("Next: %s goes on air through the timed lane (the engine held %s)", entry_id, state.cued_entry_id)
    state.last_auto_flush = time.monotonic()
    await engine_flush(station_id)
    return True


# After an operator's change took back the pre-loaded song, the engine asks again and has
# to download the new one. A Next in that moment would end the song on air with nothing
# ready (emergency music until the download is done), so Next waits: for the engine to
# ask, then this long for the file. Only within OPERATOR_SETTLE_WINDOW of such a change;
# an ordinary Next is not delayed.
OPERATOR_SETTLE_WINDOW = 4.0
OPERATOR_ASK_WAIT = 2.0
OPERATOR_LOAD_SECONDS = 0.8


async def settle_before_next(station_id) -> float:
    """Called just before a Next. Returns the seconds waited (0 for an ordinary Next)."""
    state = get_state(station_id)
    began = time.monotonic()
    if not state.operator_flush_at or began - state.operator_flush_at > OPERATOR_SETTLE_WINDOW:
        return 0.0
    while not state.cued_entry_id and time.monotonic() - began < OPERATOR_ASK_WAIT:
        await asyncio.sleep(0.05)
    if state.cued_entry_id:
        left = OPERATOR_LOAD_SECONDS - (time.monotonic() - state.cued_at)
        if left > 0:
            await asyncio.sleep(left)
    return time.monotonic() - began


async def _take_back_before_hard_item(db: AsyncSession, station_id, state: EngineState) -> None:
    """The engine already holds its next song, and that song would start seconds before
    a hard timed item: take it back (flush), so the engine asks again and is held.

    The engine does not always ask at the moment a song starts: around a Next it can
    load a song further ahead, before the rule in handle_next could apply to it (harness
    song_ends_before_hour, one run in six: the pre-loaded song played 1.5 s and was cut).
    """
    if state.engine_proto < ENGINE_PROTO_HOLD or not state.cued_entry_id:
        return
    res = await db.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current_id = res.scalars().first()
    if current_id is None or str(current_id) == state.cued_entry_id:
        return  # the pre-loaded entry is the one starting now
    # Would the song after the one on air start in the hold window?
    if await _hold_before_hard_item(db, station_id, None):
        logger.info("Taking back pre-loaded entry %s: it would start seconds before a hard item",
                    state.cued_entry_id)
        await engine_flush(station_id)


# Alert when the item on air is still "on air" this long after its known end
OVERRUN_ALERT_SECONDS = 30.0


async def _check_overrun(db: AsyncSession, station_id, state: EngineState, now) -> datetime | None:
    """Main stalled at the end of a song for two minutes with no alert (2026-11-17 02:57Z):
    the engine never reported the next start. Say so within a minute.

    Returns when the item on air ends (None when nothing is on air or its length is
    unknown), for the "not responding" watchdog."""
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    end = _airs_until(current) if current is not None else None
    if end is None or (now - end).total_seconds() < OVERRUN_ALERT_SECONDS:
        return end
    if state.overrun_alerted == str(current.id):
        return end
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
    return end


async def _hand_over_timed(db: AsyncSession, station_id, state: EngineState, now, horizon) -> None:
    """Hand hard-timed items due by `horizon` to the engine's timed lane.

    Also items whose 45 s window was missed (backend restart, slow pass) while they are
    at most MISSED_HAND_OVER_SECONDS late: the engine airs a past `at` at once. Each
    group (same time) goes over at once, its items in parallel, so a slow or hung engine
    costs the scheduler pass one command timeout per group instead of one per item; the
    engine puts a group back in order by `seq`. Groups go one after another.
    """
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            hard_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at > now - timedelta(seconds=MISSED_HAND_OVER_SECONDS),
            QueueEntry.preempt_at <= horizon,
        )
        # Handed over in play order: the engine plays a group (same time) in that order
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )
    # Never hand over what the engine already holds in its music lane, or it airs twice
    held = set(state.given) | set(state.recent_started)
    if state.cued_entry_id:
        held.add(state.cued_entry_id)
    blacked_out: bool | None = None
    groups: dict[datetime, list[tuple[QueueEntry, str]]] = {}
    for entry in res.unique().scalars().all():
        key = str(entry.id)
        if key in state.timed_sent or key in held:
            continue
        at = entry.preempt_at if entry.preempt_at.tzinfo else entry.preempt_at.replace(tzinfo=UTC)
        if at <= now and (entry.asset is None or entry.asset.asset_type != "silence"):
            # A missed item does not go on air during Shabbos / Yom Tov
            if blacked_out is None:
                from app.api.v1.queue import _is_blacked_out

                blacked_out = await _is_blacked_out(db, station_id)
            if blacked_out:
                continue
        uri = build_uri(entry, "timed")
        if not uri:
            continue
        groups.setdefault(at, []).append((entry, uri))

    handed: list[tuple[str, str]] = []
    missed: list[tuple[QueueEntry, datetime]] = []
    parallel = state.engine_proto >= ENGINE_PROTO_GROUPS
    for at, items in groups.items():
        # A group moved to follow an earlier one (timed_groups) is handed over at that
        # group's time, so the engine plays it after it as part of the same take-over
        # instead of taking over again and cutting it. Only while it is still ahead:
        # handed later, the earlier group has ended and it must take over on its own.
        send_at = at.timestamp()
        leader = state.follows.get(str(items[0][0].id))
        if leader is not None and (at - now).total_seconds() > 1.0:
            send_at = leader
        if parallel:
            oks = await asyncio.gather(*(
                engine_timed(station_id, uri, send_at, str(entry.id), seq=i, of=len(items))
                for i, (entry, uri) in enumerate(items)
            ))
        else:
            # An older engine plays a group in the order it receives it: one at a time
            oks = [
                await engine_timed(station_id, uri, send_at, str(entry.id))
                for entry, uri in items
            ]
        for (entry, _), ok in zip(items, oks):
            key = str(entry.id)
            if ok:
                state.timed_sent.add(key)
                _note_handed(state, entry)
                handed.append((key, str(entry.asset_id)))
                logger.info("Handed timed entry %s to the engine for %s", key, at.isoformat())
            elif at <= now and key not in state.timed_alerted:
                # Its time has come and the engine has not taken it: it will not air on time
                state.timed_alerted.add(key)
                missed.append((entry, at))
    if missed:
        # One alert for all of them: an engine that cannot be reached at candle lighting
        # has hundreds of Silence rows due, and each one alerted on every pass (30,600
        # alert calls in 5 minutes in the simulator, 2026-11-19)
        entry, at = missed[0]
        title = (entry.asset.title if entry.asset else None) or "A timed item"
        more = f" and {len(missed) - 1} more timed item(s)" if len(missed) > 1 else ""
        await _alert(
            db,
            station_id,
            state,
            force=True,
            title="Timed item did not reach the playout engine",
            message=(
                f"\"{title}\" was due at {at:%H:%M:%S} UTC{more}, but the engine did not "
                "accept it. It is retried for a few minutes; check that the engine "
                "is running."
            ),
        )
    if len(state.timed_alerted) > 5000:
        # Forget half, not all: clearing it made every missed item alert again
        state.timed_alerted = set(list(state.timed_alerted)[2500:])
    await _record_handed(db, station_id, handed)


async def _prune_handoffs(db: AsyncSession, station_id, state: EngineState) -> None:
    mono = time.monotonic()
    if state.last_handoff_prune and mono - state.last_handoff_prune < HANDOFF_PRUNE_SECONDS:
        return
    state.last_handoff_prune = mono
    try:
        await db.execute(
            delete(EngineHandoff).where(
                EngineHandoff.station_id == station_id,
                EngineHandoff.handed_at < datetime.now(UTC) - timedelta(hours=HANDOFF_KEEP_HOURS),
            )
        )
        await db.commit()
    except Exception as e:
        logger.warning("Could not prune engine hand-over records: %s", e)
        await db.rollback()


_background: set = set()


async def _tell_live(station_id, state: EngineState, on: bool) -> None:
    """Tell the engine once; an engine that does not answer is asked again every 15 s
    (not on every pass: each try can cost the pass a command timeout)."""
    if state.live_sent is on or (state.live_sent is None and not on):
        # (Not known and no show on: an engine still quiet from a show that ended
        # while we were restarting says so with its next ask, see handle_next)
        return
    if time.monotonic() - state.live_tried < 15.0:
        return
    state.live_tried = time.monotonic()
    await engine_live(station_id, on)


async def tick(db: AsyncSession, station) -> None:
    """Called by the scheduler loop for the engine-driven station."""
    station_id = station.id
    state = get_state(station_id)
    now = datetime.now(UTC)

    # During a live show the automation queue is left alone. The engine's pre-loaded next
    # track would air during the show and be used up: drop it once (/next hands it
    # nothing while the show is on, so nothing is cued again)
    if (station.automation_config or {}).get("live_show_id"):
        try:
            await _soft_items_during_show(db, station, state, now)
        except Exception:
            logger.exception("Station %s: soft timed items during the live show", station_id)
        if state.cued_entry_id and state.cued_entry_id != state.cued_for_after_show:
            await engine_flush(station_id)
        # The stream goes quiet under the host. Sent again after an engine or backend
        # restart during the show (the engine would play its emergency music).
        await _tell_live(station_id, state, True)
        return
    if _show_about_to_be_heard(station_id):
        # Quieted a second before the host is heard; the take-over follows. A pass that
        # fell into that second told the engine "the show is over": a song started and
        # was cut 0.9 s later (harness live_show_host_gone, 2026-11-18)
        return
    # The show is over (or we do not know what the engine was told): music again
    state.after_show_recued.clear()
    state.show_preloaded = []
    await _tell_live(station_id, state, False)

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
    # A group due while an earlier short group is still playing follows it (V1-10b)
    try:
        from app.services.timed_groups import space_hard_groups

        moved = await space_hard_groups(db, station_id, now, horizon, frozen=set(state.timed_sent))
        if moved:
            await db.commit()
            state.follows.update({k: v.timestamp() for k, v in moved.items()})
            if len(state.follows) > 200:
                state.follows = dict(list(state.follows.items())[-100:])
    except Exception as e:
        logger.warning("Spacing timed groups for station %s failed: %s", station_id, e)
        await db.rollback()
        await db.refresh(station)
    await _hand_over_timed(db, station_id, state, now, horizon)
    try:
        await _analyse_ahead(db, station_id, now)
    except Exception as e:
        logger.warning("Analysing upcoming items for station %s failed: %s", station_id, e)
        await db.rollback()

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
                # Silence already in the engine's timed lane (a blackout's silence is
                # hard-timed at its start, so all of it went there at once) is dropped
                # there too: /flush clears only the music lane, and /skip would just
                # start the next silence
                res = await db.execute(
                    select(QueueEntry.id).where(
                        QueueEntry.station_id == station_id,
                        QueueEntry.asset_id == current.asset_id,
                        QueueEntry.status == "pending",
                    )
                )
                in_lane = [str(i) for i in res.scalars().all() if str(i) in state.timed_sent]
                if state.engine_proto < ENGINE_PROTO_GROUPS:
                    in_lane = []  # an older engine has no /timed_drop: flush + skip only
                if in_lane:
                    await db.execute(
                        update(QueueEntry)
                        .where(QueueEntry.id.in_([uuid.UUID(i) for i in in_lane]))
                        .values(status="played")
                        .execution_options(synchronize_session=False)
                    )
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
                if in_lane:
                    await engine_timed_drop(station_id, in_lane)
                    state.timed_sent.difference_update(in_lane)
                await engine_flush(station_id)
                await engine_skip(station_id)
                logger.info(
                    "Blackout ended: cleared silence on engine-driven station %s", station_id
                )
    except Exception as e:
        logger.warning("Blackout check in playout tick failed: %s", e)

    await _prune_handoffs(db, station_id, state)

    # 3. The queue changed after the engine pre-loaded its next track: make it ask again
    if state.cued_entry_id:
        await _flush_if_head_changed(db, station_id, state, now)
    try:
        await _take_back_before_hard_item(db, station_id, state)
    except Exception as e:
        logger.warning("Take-back before a hard item failed for station %s: %s", station_id, e)
        await db.rollback()

    # 4. "After the song, but no later than hard_by": the song is still on, so fade it
    await _enforce_hard_by(db, station_id, state, now)

    # 5. Watchdog: the item on air runs well past its end and nothing new was reported
    on_air_until = await _check_overrun(db, station_id, state, now)
    # The engine calls only between items (/next for the one after, /started), so it is
    # quiet for as long as the item on air lasts: a song or shiur over two minutes is not
    # a dead engine. Until it first calls, silence counts from the first tick, so an
    # engine that is down when the backend starts is reported too.
    mono = time.monotonic()
    if not state.watch_since:
        state.watch_since = mono
    heard = state.last_contact or state.watch_since
    expected_quiet = on_air_until is not None and on_air_until > now
    if not expected_quiet and mono - heard > ENGINE_SILENT_ALERT_SECONDS:
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
