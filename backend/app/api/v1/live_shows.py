"""
Live Shows API — create, manage, and control live broadcasts with call-in support.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_admin, require_manager
from app.core.exceptions import NotFoundError, ConflictError, BadRequestError
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.user import User
from app.schemas.live_show import (
    LiveShowCreate,
    LiveShowUpdate,
    LiveShowInDB,
    LiveShowListResponse,
    CallInRequestInDB,
    CallInRequestListResponse,
    ScreenerAction,
)
from app.services import live_show_service
from app.config import settings
from app.services.twilio_voice_service import generate_hold_twiml, generate_no_show_twiml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-shows", tags=["live-shows"])


# --- Show CRUD ---

@router.post("", response_model=LiveShowInDB, status_code=201)
async def create_show(
    body: LiveShowCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Create a new live show."""
    show = await live_show_service.create_show(db, body, host_user_id=user.id)
    return show


@router.get("", response_model=LiveShowListResponse)
async def list_shows(
    station_id: str | None = Query(None),
    status: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """List live shows with optional filters."""
    shows, total = await live_show_service.list_shows(
        db, station_id=station_id, status=status, skip=skip, limit=limit,
    )
    return LiveShowListResponse(shows=shows, total=total)


@router.get("/{show_id}", response_model=LiveShowInDB)
async def get_show(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Get a live show by ID."""
    show = await live_show_service.get_show(db, show_id)
    if not show:
        raise NotFoundError("Live show not found")
    return show


@router.patch("/{show_id}", response_model=LiveShowInDB)
async def update_show(
    show_id: uuid.UUID,
    body: LiveShowUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Update a live show."""
    show = await live_show_service.update_show(db, show_id, body)
    if not show:
        raise NotFoundError("Live show not found")
    return show


@router.delete("/{show_id}", status_code=204)
async def delete_show(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Delete a live show (admin only)."""
    deleted = await live_show_service.delete_show(db, show_id)
    if not deleted:
        raise NotFoundError("Live show not found")


# --- Show lifecycle ---

@router.post("/{show_id}/start", response_model=LiveShowInDB)
async def start_show(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Start a live show (go live)."""
    show = await live_show_service.start_show(db, show_id, host_user_id=user.id)
    if not show:
        raise NotFoundError("Live show not found")

    # Broadcast WS event
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "show_started", {"show_id": str(show_id)})
    except Exception:
        pass

    return show


@router.post("/{show_id}/end", response_model=LiveShowInDB)
async def end_show(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """End a live show."""
    show = await live_show_service.end_show(db, show_id)
    if not show:
        raise NotFoundError("Live show not found")

    # Broadcast WS event
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "show_ended", {"show_id": str(show_id)})
    except Exception:
        pass

    return show


@router.get("/{show_id}/time-remaining")
async def time_remaining(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Get seconds until hard stop."""
    show = await live_show_service.get_show(db, show_id)
    if not show:
        raise NotFoundError("Live show not found")
    seconds = live_show_service.get_seconds_until_hard_stop(show)
    return {"seconds": seconds}


# --- Call management ---

@router.get("/{show_id}/calls", response_model=CallInRequestListResponse)
async def list_calls(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """List all callers for a show."""
    calls = await live_show_service.get_show_calls(db, show_id)
    return CallInRequestListResponse(calls=calls, total=len(calls))


@router.post("/{show_id}/calls/{call_id}/approve", response_model=CallInRequestInDB)
async def approve_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Approve a caller."""
    call = await live_show_service.approve_call(db, call_id, screened_by=user.id)
    if not call:
        raise NotFoundError("Call not found")

    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "caller_updated", {
            "call_id": str(call_id), "status": "approved",
        })
    except Exception:
        pass

    return call


@router.post("/{show_id}/calls/{call_id}/reject", response_model=CallInRequestInDB)
async def reject_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Reject a caller."""
    call = await live_show_service.reject_call(db, call_id, screened_by=user.id)
    if not call:
        raise NotFoundError("Call not found")

    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "caller_removed", {
            "call_id": str(call_id), "reason": "rejected",
        })
    except Exception:
        pass

    return call


@router.post("/{show_id}/calls/{call_id}/on-air", response_model=CallInRequestInDB)
async def put_on_air(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Push an approved caller on air. Only one caller can be on_air at a time (409 if attempted)."""
    call = await live_show_service.put_caller_on_air(db, show_id, call_id)
    if not call:
        raise NotFoundError("Call not found")

    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "caller_updated", {
            "call_id": str(call_id), "status": "on_air",
        })
    except Exception:
        pass

    return call


@router.post("/{show_id}/calls/{call_id}/end-call", response_model=CallInRequestInDB)
async def end_active_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """End an active call."""
    call = await live_show_service.end_call(db, call_id)
    if not call:
        raise NotFoundError("Call not found")

    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "caller_removed", {
            "call_id": str(call_id), "reason": "ended",
        })
    except Exception:
        pass

    return call


@router.patch("/{show_id}/calls/{call_id}", response_model=CallInRequestInDB)
async def update_call_info(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    body: ScreenerAction,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Update caller name/notes."""
    call = await live_show_service.update_call_info(
        db, call_id, caller_name=body.caller_name, notes=body.notes,
    )
    if not call:
        raise NotFoundError("Call not found")

    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "caller_updated", {
            "call_id": str(call_id),
            "caller_name": call.caller_name,
            "notes": call.notes,
        })
    except Exception:
        pass

    return call


# --- Twilio webhooks (no auth — validated by Twilio signature) ---

@router.post("/twilio/inbound")
async def twilio_inbound(request: Request, db: AsyncSession = Depends(get_db)):
    """Twilio inbound call webhook — find active show, create CallInRequest, return hold TwiML."""
    form = await request.form()
    caller_phone = form.get("From", "unknown")
    call_sid = form.get("CallSid", "")

    # Find an active live show with calls enabled
    result = await db.execute(
        select(LiveShow).where(
            LiveShow.status == LiveShowStatus.LIVE,
            LiveShow.calls_enabled == True,
        ).limit(1)
    )
    show = result.scalar_one_or_none()

    if not show:
        return Response(content=generate_no_show_twiml(), media_type="application/xml")

    # Create call-in request
    now = datetime.now(timezone.utc)
    call_request = CallInRequest(
        live_show_id=show.id,
        caller_phone=caller_phone,
        status=CallStatus.WAITING,
        twilio_call_sid=call_sid,
        hold_start=now,
    )
    db.add(call_request)
    await db.flush()
    await db.refresh(call_request)

    # Broadcast WS event
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show.id), "caller_queued", {
            "call_id": str(call_request.id),
            "caller_phone": caller_phone,
            "hold_start": now.isoformat(),
        })
    except Exception:
        pass

    twiml = generate_hold_twiml(str(show.id))
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Twilio call status callback — update call status when caller hangs up."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")

    if call_status in ("completed", "busy", "no-answer", "canceled", "failed"):
        result = await db.execute(
            select(CallInRequest).where(CallInRequest.twilio_call_sid == call_sid)
        )
        call = result.scalar_one_or_none()
        if call and call.status not in (CallStatus.COMPLETED, CallStatus.REJECTED):
            call.status = CallStatus.ABANDONED
            await db.flush()

            # Broadcast removal
            try:
                from app.api.v1.live_shows_ws import broadcast_show_event
                await broadcast_show_event(str(call.live_show_id), "caller_removed", {
                    "call_id": str(call.id), "reason": "abandoned",
                })
            except Exception:
                pass

    return Response(content="<Response/>", media_type="application/xml")


@router.get("/twilio/hold-music")
async def twilio_hold_music():
    """Return TwiML with hold music URL for Twilio conference waitUrl."""
    hold_music_url = settings.LIVE_SHOW_HOLD_MUSIC_URL or "http://com.twilio.music.classical.s3.amazonaws.com/BusssyBoy.mp3"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play loop="0">{hold_music_url}</Play>
</Response>"""
    return Response(content=twiml, media_type="application/xml")
