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
        station_id=data.station_id,
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
    return True



def _aware(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)



# Timed items that came due during the show and are this late are not played
# afterwards (a time check for an hour already past)
STALE_TIMED_AFTER = timedelta(minutes=2)


async def _pause_automation(db: AsyncSession, station_id, now: datetime) -> None:
    """The host is on air: stop the item playing and queue it again first, to carry
    on from where it stopped when the show ends. Nothing plays meanwhile (dead-air
    recovery is off while the station has a live show)."""
    from sqlalchemy import update

    from app.models.play_log import PlayLog
    from app.models.queue_entry import QueueEntry, resume_source
    from app.services import long_items

    current = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )).scalars().first()
    if current is None:
        return
    if current.started_at:
        db.add(PlayLog(id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                       start_utc=current.started_at, end_utc=now, source="scheduler"))
    at = long_items.resume_point(current, now)
    current.status = "played"
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


async def _resume_automation(db: AsyncSession, station_id, now: datetime) -> None:
    """The show is over: drop time checks that came due long ago, then start the
    queue (the item the show paused comes first)."""
    from sqlalchemy import update

    from app.models.queue_entry import QueueEntry, hard_timed

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

    # Signal scheduler to pause for this station
    station_result = await db.execute(select(Station).where(Station.id == show.station_id))
    station = station_result.scalar_one_or_none()
    if station:
        config = dict(station.automation_config or {})
        config["live_show_id"] = str(show.id)
        station.automation_config = config
        await _pause_automation(db, station.id, now)

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


async def end_show(db: AsyncSession, show_id: UUID | str) -> LiveShow | None:
    """End a live show — set status, clear scheduler signal, end all calls, stop Icecast."""
    result = await db.execute(select(LiveShow).where(LiveShow.id == show_id))
    show = result.scalar_one_or_none()
    if not show:
        return None

    now = datetime.now(timezone.utc)
    show.status = LiveShowStatus.ENDED
    show.actual_end = now

    # Clear scheduler signal
    station_result = await db.execute(select(Station).where(Station.id == show.station_id))
    station = station_result.scalar_one_or_none()
    if station and station.automation_config:
        config = dict(station.automation_config)
        config.pop("live_show_id", None)
        station.automation_config = config
        await _resume_automation(db, station.id, now)

    # End all active calls
    try:
        from app.services.twilio_voice_service import end_all_calls_for_show
        await end_all_calls_for_show(db, show_id)
    except Exception as e:
        logger.warning("Failed to end calls for show %s: %s", show_id, e)

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
    show = await end_show(db, show_id)
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


async def put_caller_on_air(
    db: AsyncSession,
    show_id: UUID | str,
    call_id: UUID | str,
) -> CallInRequest | None:
    """Put an approved caller on air. Only one caller can be on_air at a time."""
    from app.core.exceptions import ConflictError

    # Check if another caller is already on air
    existing = await db.execute(
        select(CallInRequest).where(
            CallInRequest.live_show_id == show_id,
            CallInRequest.status == CallStatus.ON_AIR,
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Another caller is already on air")

    call = await get_call(db, call_id)
    if not call:
        return None

    now = datetime.now(timezone.utc)
    call.status = CallStatus.ON_AIR
    call.air_start = now

    # Move caller to live conference via Twilio
    try:
        from app.services.twilio_voice_service import move_caller_to_conference
        show = await get_show(db, show_id)
        if show and call.twilio_call_sid:
            conference_name = f"live-show-{show_id}"
            await move_caller_to_conference(call.twilio_call_sid, conference_name)
    except Exception as e:
        logger.warning("Failed to move caller %s to conference: %s", call_id, e)

    await db.flush()
    await db.refresh(call)
    return call


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
