"""
Live show lifecycle management — create, start, end, hard stop.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_show import LiveShow, LiveShowStatus
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.station import Station
from app.schemas.live_show import LiveShowCreate, LiveShowUpdate

logger = logging.getLogger(__name__)


async def create_show(
    db: AsyncSession,
    data: LiveShowCreate,
    host_user_id: UUID | None = None,
) -> LiveShow:
    """Create a new live show."""
    show = LiveShow(
        station_id=UUID(str(data.station_id)),  # the schema allows str (SQLite tests); the column wants a UUID
        host_user_id=host_user_id,
        title=data.title,
        description=data.description,
        broadcast_mode=data.broadcast_mode,
        scheduled_start=data.scheduled_start,
        scheduled_end=data.scheduled_end,
        calls_enabled=data.calls_enabled,
    )
    db.add(show)
    await db.flush()
    await db.refresh(show)
    return show


async def update_show(
    db: AsyncSession,
    show_id: UUID | str,
    data: LiveShowUpdate,
) -> LiveShow:
    """Update a live show's details."""
    result = await db.execute(select(LiveShow).where(LiveShow.id == show_id))
    show = result.scalar_one_or_none()
    if not show:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(show, field, value)

    await db.flush()
    await db.refresh(show)
    return show


async def get_show(db: AsyncSession, show_id: UUID | str) -> LiveShow | None:
    """Get a single live show by ID."""
    result = await db.execute(select(LiveShow).where(LiveShow.id == show_id))
    return result.scalar_one_or_none()


async def list_shows(
    db: AsyncSession,
    station_id: UUID | str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[LiveShow], int]:
    """List live shows with optional filters."""
    query = select(LiveShow).order_by(LiveShow.created_at.desc())
    count_query = select(func.count()).select_from(LiveShow)

    if station_id:
        query = query.where(LiveShow.station_id == station_id)
        count_query = count_query.where(LiveShow.station_id == station_id)
    if status:
        query = query.where(LiveShow.status == status)
        count_query = count_query.where(LiveShow.status == status)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all(), total


async def delete_show(db: AsyncSession, show_id: UUID | str) -> bool:
    """Delete a live show."""
    result = await db.execute(select(LiveShow).where(LiveShow.id == show_id))
    show = result.scalar_one_or_none()
    if not show:
        return False
    await db.delete(show)
    await db.flush()  # surface a failure here, not after the answer was sent
    return True



def _aware(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)



# Timed items that came due during the show and are this late are not played
# afterwards (a time check for an hour already past)
STALE_TIMED_AFTER = timedelta(minutes=2)


async def soft_items_wait_for_show(db: AsyncSession, station_id, now: datetime) -> list[str]:
    """Timed items that must not be lost to a live show go back to waiting (soft), so
    they play when the show is over. Two kinds:

    - a soft item that was made hard only to pause a shiur or story (`was_soft`): the
      show took the air instead, and a hard item that comes due under the host is
      closed when the show ends (Jerusalem 2026-11-18: a contracted sponsorship due at
      19:15:00 turned hard at 19:14, the show took over at 19:14:49, and it never aired);
    - a sponsorship that is hard by the station's timing choice, once its time has
      come during the show.

    Not an item the show itself is about to air over the delay (live_inserts). Returns
    the ids changed; on an engine station the caller takes them out of the timed lane."""
    from sqlalchemy import and_, or_

    from app.models.queue_entry import QueueEntry
    from app.services import live_inserts

    rows = (await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.timing_mode == "hard",
            or_(
                QueueEntry.was_soft.is_(True),
                and_(QueueEntry.source == "ad_slot", QueueEntry.preempt_at <= now),
            ),
        )
    )).scalars().all()
    changed = []
    for entry in rows:
        if str(entry.id) in live_inserts._preparing:
            continue
        entry.timing_mode = "soft"
        entry.hard_by = None
        changed.append(str(entry.id))
    if changed:
        await db.flush()
        logger.info("Station %s: %d timed item(s) wait for the end of the live show: %s",
                    station_id, len(changed), ", ".join(changed))
    return changed


async def _pause_automation(db: AsyncSession, station_id, now: datetime) -> None:
    """The host is on air: stop the item playing and queue it again first, to carry
    on from where it stopped when the show ends. Nothing plays meanwhile (dead-air
    recovery is off while the station has a live show)."""
    from sqlalchemy import update

    from app.models.play_log import PlayLog
    from app.models.queue_entry import RESUME_PREFIX, QueueEntry, aired_from, resume_source
    from app.services import long_items

    waiting = await soft_items_wait_for_show(db, station_id, now)
    if waiting:
        from app.config import settings
        if settings.engine_drives(station_id):
            try:
                from app.services import playout_engine
                await playout_engine.take_back_timed(station_id, waiting)
            except Exception as e:
                logger.warning("Station %s: could not take timed items back from the engine: %s", station_id, e)
    current = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )).scalars().first()
    if current is None:
        return
    if current.started_at:
        db.add(PlayLog(id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                       start_utc=aired_from(current), end_utc=now, source="scheduler"))
    at = long_items.resume_point(current, now)
    current.status = "played"
    if current.preempt_at is not None:
        # A time check or other timed item: half of it after the show would be a stale
        # time (the engine harness heard it come back after End, 2026-11-18)
        await _air_cut_item_over_the_delay(db, station_id, current, now)
        return
    # An earlier plan to carry this item on is void: a timed group was about to pause
    # it (long_items queued its second part), or an earlier show paused it and the part
    # after that show was itself cut by this one. Left in the queue, each of them played
    # after the show: a story's same minutes aired three times (Jerusalem, 2026-11-18)
    from sqlalchemy import delete
    await db.execute(
        delete(QueueEntry)
        .where(
            QueueEntry.station_id == station_id, QueueEntry.status == "pending",
            QueueEntry.asset_id == current.asset_id,
            QueueEntry.source.like(f"{RESUME_PREFIX}%"),
        )
        .execution_options(synchronize_session=False)
    )
    if at is None:
        return  # it was nearly over
    first = (await db.execute(
        select(func.min(QueueEntry.position)).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )).scalar() or 1
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .values(position=QueueEntry.position + 1)
        .execution_options(synchronize_session=False)
    )
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id, position=first,
                      status="pending", source=resume_source(at)))
    await db.flush()


async def _air_cut_item_over_the_delay(db: AsyncSession, station_id, entry, now: datetime) -> None:
    """A timed item that had only just started when the show took the station (the
    engine started it early as the delay filled, and the wait for timed items had
    already looked): listeners got 0.8 s of the time check and it was gone (harness,
    2026-11-18). It goes back to waiting, so live_inserts airs it whole over the
    delay at its time. Only when the station's rule would air it there, and only
    when less than half was heard: a time check heard nearly whole is not said twice."""
    from app.models.asset import Asset
    from app.models.queue_entry import aired_from
    from app.services import live_inserts
    from app.services.live_air import settings_for

    station = await db.get(Station, station_id)
    asset = await db.get(Asset, entry.asset_id) if entry.asset_id else None
    if station is None or asset is None or not asset.duration:
        return
    _delay, _dump, rule = settings_for(station.automation_config)
    if _delay <= 0 or rule == "none" or (rule == "time" and asset.category != live_inserts.TIME_CATEGORY):
        return
    hard = entry.timing_mode == "hard" or (entry.timing_mode is None and entry.source != "ad_slot")
    if not hard:
        return
    start = aired_from(entry) if entry.started_at else now
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    heard = max(0.0, (now - start).total_seconds()) if start else 0.0
    if heard >= float(asset.duration) / 2:
        return
    entry.status = "pending"
    entry.started_at = None
    live_inserts._preparing.discard(str(entry.id))


async def _resume_automation(db: AsyncSession, station_id, now: datetime) -> None:
    """The show is over: drop time checks that came due long ago, then start the
    queue (the item the show paused comes first)."""
    from sqlalchemy import update

    from app.models.queue_entry import QueueEntry, hard_timed

    # Sponsorships whose time came during the show are not lost with the time checks
    await soft_items_wait_for_show(db, station_id, now)
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at < now - STALE_TIMED_AFTER,
            hard_timed(),
        )
        .values(status="played")
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    from app.config import settings
    if settings.engine_drives(station_id):
        # What the engine was handed and could not air under the host is closed, not
        # left waiting in the queue
        try:
            from app.services import playout_engine

            await playout_engine.close_timed_lost_to_live_show(db, station_id, now)
        except Exception as e:
            logger.error("Station %s: timed items lost to the show were not closed: %s", station_id, e)
        # The playout engine starts the next item itself (it asks /next again once the
        # show is over). start_from_queue would mark an entry "playing" here that the
        # engine was never given, and the site would show it on air.
        return
    try:
        from app.api.v1.queue import start_from_queue
        await start_from_queue(db, station_id)
    except Exception as e:
        logger.error("Station %s: could not restart the queue after the show: %s", station_id, e)


async def start_show(
    db: AsyncSession,
    show_id: UUID | str,
    host_user_id: UUID | None = None,
) -> LiveShow | None:
    """Start a live show — set status to live, signal scheduler, start Icecast mount for WebRTC mode."""
    result = await db.execute(select(LiveShow).where(LiveShow.id == show_id))
    show = result.scalar_one_or_none()
    if not show:
        return None

    now = datetime.now(timezone.utc)
    show.status = LiveShowStatus.LIVE
    show.actual_start = now
    # Started after its planned end (or with none): give it its planned length from
    # now, else one hour. A show started late used to be hard-stopped at once.
    end = _aware(show.scheduled_end)
    if end is None or end <= now:
        start = _aware(show.scheduled_start)
        planned = (end - start) if (end and start and end > start) else timedelta(hours=1)
        show.scheduled_end = now + planned
    if host_user_id:
        show.host_user_id = host_user_id

    # Take the station over: its automation pauses under the host. With a broadcast
    # delay this waits until listeners really hear the host (the delay is full), so
    # the music keeps playing while it fills: see take_over_when_heard.
    station_result = await db.execute(select(Station).where(Station.id == show.station_id))
    station = station_result.scalar_one_or_none()
    if station:
        from app.services.live_air import settings_for
        delay, _dump, _rule = settings_for(station.automation_config)
        if delay <= 0:
            await take_over_station(db, station, show, now)

    # Set icecast mount for WebRTC mode
    if show.broadcast_mode == "webrtc" and not show.icecast_mount:
        show.icecast_mount = f"/live-{show.id}"

    # Start Icecast stream if in WebRTC mode
    if show.broadcast_mode == "webrtc":
        try:
            from app.services.icecast_service import start_icecast_stream
            await start_icecast_stream(
                str(show.id), show.title, mount=show.icecast_mount
            )
        except Exception as e:
            logger.warning("Failed to start Icecast for live show %s: %s", show_id, e)

    await db.flush()
    await db.refresh(show)
    return show


async def take_over_station(db: AsyncSession, station: Station, show: LiveShow, now: datetime) -> None:
    """The host is heard from now on: the scheduler leaves the station alone, the item
    playing stops (to carry on after the show) and an engine station's stream goes quiet."""
    config = dict(station.automation_config or {})
    if config.get("live_show_id") == str(show.id):
        return
    config["live_show_id"] = str(show.id)
    station.automation_config = config
    await _pause_automation(db, station.id, now)
    from app.config import settings as _settings
    if _settings.engine_drives(station.id):
        # At once, not on the scheduler's next pass: the song must not play on
        # under the host's first words
        try:
            from app.services import playout_engine
            # Quiet first, then the flush: under the live lane the engine loads the
            # item that follows the show at once (the paused item's resume entry),
            # so music is back the moment the show ends
            await playout_engine.engine_live(station.id, True)
            await playout_engine.engine_flush(station.id)
        except Exception as e:
            logger.warning("Could not quiet the engine for the live show: %s", e)


EARLY_START = 8.0   # how early a hard timed item may start (queue.TIMED_EARLY_START_SECONDS)
HOLD_LIMIT = 45.0   # the longest a show waits for timed items before it is heard anyway


async def timed_item_ends(db: AsyncSession, station_id, now: datetime, within: float) -> datetime | None:
    """When the timed item on air (or a hard one due in the next `within` seconds) ends.
    None when there is none. A show whose delay fills meanwhile waits for that moment:
    taking the station over cut a time check after 3.4 of its 6 s (reviewer, 2026-11-18)."""
    from sqlalchemy.orm import selectinload

    from app.models.queue_entry import QueueEntry
    from app.services.playout_engine import _airs_until

    rows = (await db.execute(
        select(QueueEntry).options(selectinload(QueueEntry.asset)).where(
            QueueEntry.station_id == station_id,
            QueueEntry.preempt_at.is_not(None),
            QueueEntry.status.in_(("playing", "pending")),
            QueueEntry.preempt_at >= now - timedelta(seconds=600),
            QueueEntry.preempt_at <= now + timedelta(seconds=within),
        )
    )).scalars().all()
    ends: list[datetime] = []
    for entry in rows:
        if entry.status == "playing":
            end = _airs_until(entry)
        else:
            hard = entry.timing_mode == "hard" or (entry.timing_mode is None and entry.source != "ad_slot")
            due = entry.preempt_at if entry.preempt_at.tzinfo else entry.preempt_at.replace(tzinfo=timezone.utc)
            if not hard or due < now - timedelta(seconds=60) or entry.asset is None or not entry.asset.duration:
                continue
            end = max(due, now) + timedelta(seconds=float(entry.asset.duration))
        if end is not None and end > now:
            ends.append(end)
    return max(ends) if ends else None


async def hold_for_timed_items(station_id: str, air) -> None:
    """While the show's delay fills, keep it from going to listeners in the middle of a
    timed item. Looks once a second; the host's words wait in the delay meanwhile."""
    import asyncio

    from app.db.session import get_async_session

    started = air.clock()
    while not (air.listening or air.closed or air.finishing):
        try:
            async for db in get_async_session():
                now = datetime.now(timezone.utc)
                # Also an item due up to EARLY_START after that: when the song before
                # it ends early the player starts the item early, and a take-over in
                # that moment left 0.8 s of the time check on the stream, with the
                # whole check again over the delay 8 s later (harness, 2026-11-18)
                within = max(0.0, air.target - air.buffered) + 2.0 + EARLY_START
                end = await timed_item_ends(db, uuid.UUID(str(station_id)), now, within)
                await db.rollback()
                if end is not None and not air.told_soon:
                    # (Not once the stream server was told to go quiet: holding the
                    # show longer then would leave the stream silent over the item)
                    # The stream server is told to go quiet SOON before the show is
                    # heard, so that word leaves as the item ends, not during it
                    from app.services.live_air import SOON
                    until = air.clock() + (end - now).total_seconds() + SOON
                    air.hold_until = min(until, started + air.target + HOLD_LIMIT)
                break
        except Exception:
            logger.warning("Could not look for timed items before live show on %s", station_id, exc_info=True)
        await asyncio.sleep(1.0)


async def take_over_when_heard(station_id: str, show_id: str) -> None:
    """Listeners just started hearing the show through its delay: take the station over now."""
    from app.db.session import get_async_session

    async for db in get_async_session():
        try:
            show = await get_show(db, uuid.UUID(str(show_id)))
            station = await db.get(Station, uuid.UUID(str(station_id)))
            if show is not None and station is not None and show.status == LiveShowStatus.LIVE:
                await take_over_station(db, station, show, datetime.now(timezone.utc))
                await db.commit()
                logger.info("Live show %s is heard on station %s: automation paused", show_id, station_id)
        except Exception:
            logger.exception("Could not take station %s over for live show %s", station_id, show_id)
            await db.rollback()
        break


async def release_station(db: AsyncSession, station_id, show_id, now: datetime) -> bool:
    """The show is over for listeners: the scheduler and the queue (or the engine) carry on."""
    station = await db.get(Station, station_id)
    if station is None:
        return False
    config = dict(station.automation_config or {})
    if config.get("live_show_id") != str(show_id):
        return False  # never taken over (the delay never filled): the music never stopped
    config.pop("live_show_id", None)
    station.automation_config = config
    await _resume_automation(db, station.id, now)
    from app.config import settings as _settings
    if _settings.engine_drives(station.id):
        # At once, not on the scheduler's next pass (up to a second or two more of
        # quiet): the item that follows the show is already loaded
        try:
            from app.services import playout_engine
            await playout_engine.engine_live(station.id, False)
        except Exception as e:
            logger.warning("Could not tell the engine the live show is over: %s", e)
    return True


async def _release_later(station_id: str, show_id: str) -> None:
    from app.db.session import get_async_session

    async for db in get_async_session():
        try:
            if await release_station(db, uuid.UUID(station_id), show_id, datetime.now(timezone.utc)):
                await db.commit()
                logger.info("Live show %s played out of the delay: station %s carries on", show_id, station_id)
        except Exception:
            logger.exception("Could not hand station %s back after live show %s", station_id, show_id)
            await db.rollback()
        break


async def start_if_scheduled(
    db: AsyncSession, show_id: UUID | str, host_user_id: UUID | None = None,
) -> bool:
    """Start the show when its host starts broadcasting and it is not live yet.

    The Host Console's "Start Broadcasting" only opens the microphone. Without this
    the show stayed "scheduled", so the station's automation was never paused and a
    story under the show was not resumed where it stopped (live test, 2026-11-16).
    """
    show = await get_show(db, show_id)
    if show is None or show.status != LiveShowStatus.SCHEDULED:
        return False
    await start_show(db, show.id, host_user_id=host_user_id)
    return True


def _with_state(relay, station_key: str, then):
    """After the delay played out: hand the station back."""
    async def run():
        relay.airs.pop(station_key, None)  # listeners were told when it stopped airing
        await then()
    return run


async def end_show(db: AsyncSession, show_id: UUID | str, drain: bool = True) -> LiveShow | None:
    """End a live show — set status, clear scheduler signal, end all calls, stop Icecast."""
    result = await db.execute(select(LiveShow).where(LiveShow.id == show_id))
    show = result.scalar_one_or_none()
    if not show:
        return None

    now = datetime.now(timezone.utc)
    show.status = LiveShowStatus.ENDED
    show.actual_end = now

    # Listeners still have the end of the show waiting in the broadcast delay: it plays
    # out (wound down quickly) and the station carries on after it. A hard stop does
    # not wait (the delay winds down by itself before it). Without a delay, at once.
    from app.api.v1.live_shows_ws import live_relay
    station_key = str(show.station_id)
    air = live_relay.airs.get(station_key)
    if drain and air is not None and air.show_id == str(show.id) and air.listening and air.buffered > 0.3:
        async def later(_station=station_key, _show=str(show.id)):
            await _release_later(_station, _show)
        try:
            await live_relay.set_on_air(station_key, None, drain=True,
                                        on_done=_with_state(live_relay, station_key, later))
        except Exception as e:
            logger.warning("Could not let show %s play out of the delay: %s", show_id, e)
    else:
        await release_station(db, show.station_id, show.id, now)
        # The host is off air from this moment, even if the host's page stays open
        try:
            if live_relay.on_air.get(station_key) == str(show.id) or station_key in live_relay.airs:
                await live_relay.set_on_air(station_key, None, drain=False)
        except Exception as e:
            logger.warning("Could not take the host off air for show %s: %s", show_id, e)

    # End all active calls
    try:
        from app.services.twilio_voice_service import end_all_calls_for_show
        await end_all_calls_for_show(db, show_id)
    except Exception as e:
        logger.warning("Failed to end calls for show %s: %s", show_id, e)

    # Web callers: their calls end with the show
    try:
        from app.api.v1.live_shows_ws import live_relay
        await live_relay.drop_cohosts(str(show.id))
        for line in [c for c in live_relay.callers.values() if c.show_id == str(show.id) and not c.cohost]:
            call = await get_call(db, line.call_id)
            if call is not None and call.status in (CallStatus.WAITING, CallStatus.SCREENING,
                                                    CallStatus.APPROVED, CallStatus.ON_AIR):
                if call.status == CallStatus.ON_AIR:
                    call.air_end = now
                call.status = CallStatus.COMPLETED
            await live_relay.caller_status(line.call_id, "completed")
    except Exception as e:
        logger.warning("Failed to end web calls for show %s: %s", show_id, e)

    # Stop Icecast stream
    if show.broadcast_mode == "webrtc":
        try:
            from app.services.icecast_service import stop_icecast_stream
            await stop_icecast_stream(str(show.id))
        except Exception as e:
            logger.warning("Failed to stop Icecast for show %s: %s", show_id, e)

    await db.flush()
    await db.refresh(show)
    return show


async def hard_stop_show(db: AsyncSession, show_id: UUID | str) -> LiveShow | None:
    """Hard stop a live show (triggered by scheduler at scheduled_end) — same as end_show + creates alert."""
    show = await end_show(db, show_id, drain=False)
    if not show:
        return None

    # Create system alert
    try:
        from app.services.alert_service import create_alert
        await create_alert(
            db,
            alert_type="live_show",
            severity="warning",
            title=f"Live show hard stopped: {show.title}",
            message=f"Live show '{show.title}' was automatically stopped because it reached the scheduled end time.",
            station_id=show.station_id,
            context={"show_id": str(show.id)},
        )
    except Exception as e:
        logger.warning("Failed to create hard stop alert for show %s: %s", show_id, e)

    return show


def get_seconds_until_hard_stop(show: LiveShow) -> float | None:
    """Get seconds remaining until scheduled_end. Returns None if no end time set."""
    if not show.scheduled_end:
        return None
    now = datetime.now(timezone.utc)
    delta = show.scheduled_end.replace(tzinfo=timezone.utc) - now if show.scheduled_end.tzinfo is None else show.scheduled_end - now
    return max(0.0, delta.total_seconds())


# --- Call management ---

async def get_show_calls(
    db: AsyncSession,
    show_id: UUID | str,
) -> list[CallInRequest]:
    """Get all calls for a live show."""
    result = await db.execute(
        select(CallInRequest)
        .where(CallInRequest.live_show_id == show_id)
        .order_by(CallInRequest.created_at)
    )
    return result.scalars().all()


async def get_call(db: AsyncSession, call_id: UUID | str) -> CallInRequest | None:
    """Get a single call by ID."""
    result = await db.execute(select(CallInRequest).where(CallInRequest.id == call_id))
    return result.scalar_one_or_none()


async def approve_call(
    db: AsyncSession,
    call_id: UUID | str,
    screened_by: UUID | None = None,
) -> CallInRequest | None:
    """Approve a caller (move from waiting/screening to approved)."""
    call = await get_call(db, call_id)
    if not call:
        return None
    call.status = CallStatus.APPROVED
    if screened_by:
        call.screened_by = screened_by
    await db.flush()
    await db.refresh(call)
    return call


async def reject_call(
    db: AsyncSession,
    call_id: UUID | str,
    screened_by: UUID | None = None,
) -> CallInRequest | None:
    """Reject a caller."""
    call = await get_call(db, call_id)
    if not call:
        return None
    call.status = CallStatus.REJECTED
    if screened_by:
        call.screened_by = screened_by

    # Hang up the call via Twilio
    try:
        from app.services.twilio_voice_service import end_caller
        await end_caller(call.twilio_call_sid)
    except Exception as e:
        logger.warning("Failed to hang up rejected call %s: %s", call_id, e)

    await db.flush()
    await db.refresh(call)
    return call


MAX_CALLERS_ON_AIR = 4


async def put_caller_on_air(
    db: AsyncSession,
    show_id: UUID | str,
    call_id: UUID | str,
) -> CallInRequest | None:
    """Put an approved caller on air. Up to MAX_CALLERS_ON_AIR can be on air together
    (the mix carries them all and each hears the host and the others); it was one."""
    from app.core.exceptions import ConflictError

    call = await get_call(db, call_id)
    if not call:
        return None
    on_air = (await db.execute(
        select(CallInRequest.id).where(
            CallInRequest.live_show_id == show_id,
            CallInRequest.status == CallStatus.ON_AIR,
            CallInRequest.id != call.id,
        )
    )).scalars().all()
    if len(on_air) >= MAX_CALLERS_ON_AIR:
        raise ConflictError(f"{MAX_CALLERS_ON_AIR} callers are on air already. Hold or end one first.")

    now = datetime.now(timezone.utc)
    call.status = CallStatus.ON_AIR
    call.air_start = now

    # Move caller to live conference via Twilio
    try:
        from app.services.twilio_voice_service import move_caller_to_conference
        show = await get_show(db, show_id)
        from app.api.v1.live_shows_ws import live_relay
        # A phone caller on a media stream is in the show's own mixer already; a
        # redirect to a conference would cut that stream
        if show and call.twilio_call_sid and str(call.id) not in live_relay.callers:
            conference_name = f"live-show-{show_id}"
            await move_caller_to_conference(call.twilio_call_sid, conference_name)
    except Exception as e:
        logger.warning("Failed to move caller %s to conference: %s", call_id, e)

    await db.flush()
    await db.refresh(call)
    return call


async def hold_caller(db: AsyncSession, call_id: UUID | str) -> CallInRequest | None:
    """Take the caller off air without hanging up: back to the approved list, still
    connected and hearing the show, ready to go on air again."""
    call = await get_call(db, call_id)
    if not call:
        return None
    if call.status != CallStatus.ON_AIR:
        from app.core.exceptions import ConflictError
        raise ConflictError("This caller is not on air")
    call.status = CallStatus.APPROVED
    await db.flush()
    await db.refresh(call)
    return call


async def next_approved_call(db: AsyncSession, show_id: UUID | str) -> CallInRequest | None:
    """The approved caller who has waited longest."""
    result = await db.execute(
        select(CallInRequest)
        .where(CallInRequest.live_show_id == show_id, CallInRequest.status == CallStatus.APPROVED)
        .order_by(CallInRequest.created_at)
    )
    return result.scalars().first()


async def end_call(
    db: AsyncSession,
    call_id: UUID | str,
) -> CallInRequest | None:
    """End an active call (from on_air or approved)."""
    call = await get_call(db, call_id)
    if not call:
        return None

    now = datetime.now(timezone.utc)
    call.status = CallStatus.COMPLETED
    if call.air_start and not call.air_end:
        call.air_end = now

    # Hang up via Twilio
    try:
        from app.services.twilio_voice_service import end_caller
        await end_caller(call.twilio_call_sid)
    except Exception as e:
        logger.warning("Failed to hang up call %s: %s", call_id, e)

    await db.flush()
    await db.refresh(call)
    return call


async def update_call_info(
    db: AsyncSession,
    call_id: UUID | str,
    caller_name: str | None = None,
    notes: str | None = None,
) -> CallInRequest | None:
    """Update caller name/notes (screener action)."""
    call = await get_call(db, call_id)
    if not call:
        return None
    if caller_name is not None:
        call.caller_name = caller_name
    if notes is not None:
        call.notes = notes
    await db.flush()
    await db.refresh(call)
    return call


# --- Dump log (models/live_dump.py) ---

def dump_wav(heard, cut) -> bytes:
    """What listeners heard just before the Dump, half a second of silence, then
    what was cut, as a WAV file (16-bit mono)."""
    import io
    import wave

    import numpy as np

    from app.services.live_air import RATE

    gap = np.zeros(RATE // 2, dtype=np.int16)
    pcm = np.concatenate((np.asarray(heard, dtype=np.int16), gap, np.asarray(cut, dtype=np.int16)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.astype("<i2").tobytes())
    return out.getvalue()


async def save_dump_audio(key: str, heard, cut) -> None:
    from app.services.storage_service import upload_file

    try:
        await upload_file(dump_wav(heard, cut), key, content_type="audio/wav")
    except Exception:
        logger.exception("Could not save the dump recording %s", key)
