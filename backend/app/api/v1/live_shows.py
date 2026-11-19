"""
Live Shows API — create, manage, and control live broadcasts with call-in support.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import station_guard as sg
from app.core.dependencies import get_db, require_admin, require_dj_or_manager, require_staff
from app.core.exceptions import ConflictError, NotFoundError
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
    DumpRequest,
    DelayRequest,
)
from app.services import caller_history, live_show_service
from app.config import settings
from app.services.twilio_voice_service import generate_hold_twiml, generate_no_show_twiml

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/live-shows",
    tags=["live-shows"],
    dependencies=[Depends(sg.station_write_guard(path={"show_id": sg.of_live_show}))],
)


# --- Show CRUD ---

@router.post("", response_model=LiveShowInDB, status_code=201)
async def create_show(
    body: LiveShowCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_staff),
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
    _user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_dj_or_manager),
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
    user: User = Depends(require_dj_or_manager),
):
    """Start a live show (go live). 409 while another show is live on the station."""
    asked = await live_show_service.get_show(db, show_id)
    if asked is not None and asked.status != LiveShowStatus.LIVE:
        other = await live_show_service.other_live_show(db, asked)
        if other is not None:
            raise ConflictError(f'"{other.title}" is live on this station. End it before starting another show.')
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
    _user: User = Depends(require_dj_or_manager),
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
    _user: User = Depends(require_dj_or_manager),
):
    """Get seconds until hard stop."""
    show = await live_show_service.get_show(db, show_id)
    if not show:
        raise NotFoundError("Live show not found")
    seconds = live_show_service.get_seconds_until_hard_stop(show)
    return {"seconds": seconds}


# --- Broadcast delay: Dump, exit, build (services/live_air.py) ---

def _air_of(show) -> "object | None":
    from app.api.v1.live_shows_ws import live_relay
    air = live_relay.airs.get(str(show.station_id))
    return air if air is not None and air.show_id == str(show.id) and not air.closed else None


def _cohosts(show_id) -> list[str]:
    from app.api.v1.live_shows_ws import live_relay
    return live_relay.cohosts(str(show_id))


async def _show_or_404(db: AsyncSession, show_id: uuid.UUID):
    show = await live_show_service.get_show(db, show_id)
    if not show:
        raise NotFoundError("Live show not found")
    return show


@router.get("/{show_id}/air")
async def air_status(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """The show's broadcast delay now: how full, building or steady, safe to Dump."""
    show = await _show_or_404(db, show_id)
    air = _air_of(show)
    if air is None:
        from app.models.station import Station
        from app.services.live_air import settings_for
        station = await db.get(Station, show.station_id)
        delay, dump, _rule = settings_for(station.automation_config if station else None)
        return {"mode": "off", "on_air": False, "delay": 0.0, "target": delay, "configured": delay,
                "dump_size": dump, "safe": False, "dumps": 0, "show_id": str(show.id),
                "cohosts": _cohosts(show.id)}
    return {**air.status(), "cohosts": _cohosts(show.id)}


@router.post("/{show_id}/dump")
async def dump(
    show_id: uuid.UUID,
    body: DumpRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
):
    """Dump: cut what was just said before listeners hear it. The newest audio waiting in
    the delay is thrown away and the delay builds back by itself. With `drop_caller` the
    caller on air is taken off as well."""
    show = await _show_or_404(db, show_id)
    air = _air_of(show)
    if air is None:
        raise HTTPException(status_code=409, detail="The show is not on air: there is nothing to dump")
    if air.configured <= 0 and air.buffered <= 0:
        raise HTTPException(
            status_code=409,
            detail="This show has no broadcast delay, so listeners have already heard it. Build the delay first.")
    if air.buffered < 0.05:
        raise HTTPException(
            status_code=409,
            detail="Nothing is waiting in the delay right now, so there is nothing to cut. It is still building back.")
    cut = air.dump()
    from app.api.v1.live_shows_ws import live_relay as _relay
    await _relay.video_cut(str(show.station_id))
    dropped = []
    dropped_name = None
    if body is not None and body.drop_caller:
        for call in await live_show_service.get_show_calls(db, show_id):
            if call.status == CallStatus.ON_AIR:
                dropped_name = call.caller_name or call.caller_phone
                await live_show_service.end_call(db, call.id)
                dropped.append(str(call.id))
        for call_id in dropped:
            await _tell_web_caller(call_id, "completed")
            try:
                from app.api.v1.live_shows_ws import broadcast_show_event
                await broadcast_show_event(str(show_id), "caller_removed", {"call_id": call_id, "reason": "dumped"})
            except Exception:
                pass
    # The dump log: who, when, how much, and a recording of what was cut
    from app.models.live_dump import LiveDump
    from app.services.live_air import RATE as _RATE
    heard, cut_pcm = air.last_dump or ((), ())
    record = LiveDump(
        id=uuid.uuid4(), live_show_id=show.id, station_id=show.station_id, user_id=user.id,
        user_email=user.email, dumped_at=datetime.now(timezone.utc), cut_seconds=round(cut, 2),
        heard_seconds=round(len(heard) / _RATE, 2), caller_name=dropped_name,
    )
    record.file_path = f"live-dumps/{show.id}/{record.id}.wav"
    db.add(record)
    await db.flush()
    import asyncio as _asyncio
    _dump_saves.add(task := _asyncio.ensure_future(
        live_show_service.save_dump_audio(record.file_path, heard, cut_pcm)))
    task.add_done_callback(_dump_saves.discard)
    logger.warning("DUMP on live show %s by %s: %.1f s cut%s", show_id, user.email, cut,
                   f", caller(s) dropped: {dropped}" if dropped else "")
    status = air.status()
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "air_dump", {"seconds": round(cut, 1), "by": user.email,
                                                              "dropped_callers": dropped})
        await broadcast_show_event(str(show_id), "air_status", status)
    except Exception:
        pass
    return {"dumped_seconds": round(cut, 1), "dropped_callers": dropped, "air": status, "dump_id": str(record.id)}


_dump_saves: set = set()  # recordings being stored (kept so the tasks are not collected)


@router.get("/{show_id}/dumps")
async def list_dumps(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """The show's dump log, newest first."""
    from app.models.live_dump import LiveDump
    rows = (await db.execute(
        select(LiveDump).where(LiveDump.live_show_id == show_id).order_by(LiveDump.dumped_at.desc())
    )).scalars().all()
    return {"dumps": [{
        "id": str(d.id), "dumped_at": d.dumped_at.isoformat() if d.dumped_at else None,
        "by": d.user_email, "cut_seconds": d.cut_seconds, "heard_seconds": d.heard_seconds,
        "caller_name": d.caller_name, "has_audio": bool(d.file_path),
    } for d in rows]}


class ChatLine(BaseModel):
    text: str


def _chat_row(m) -> dict:
    return {"id": str(m.id), "by": m.author, "user_id": str(m.user_id) if m.user_id else None,
            "text": m.text, "at": m.created_at.isoformat() if m.created_at else None}


@router.get("/{show_id}/chat")
async def list_chat(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """The show's staff chat (host and screener), oldest first, the last 200 lines."""
    from app.models.live_chat_message import LiveChatMessage
    await _show_or_404(db, show_id)
    rows = (await db.execute(
        select(LiveChatMessage).where(LiveChatMessage.live_show_id == show_id)
        .order_by(LiveChatMessage.created_at.desc(), LiveChatMessage.id.desc()).limit(200)
    )).scalars().all()
    return {"messages": [_chat_row(m) for m in reversed(rows)]}


@router.post("/{show_id}/chat")
async def say_in_chat(
    show_id: uuid.UUID,
    body: ChatLine,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
):
    """Write a line in the show's staff chat. Everyone with the Host Console or the Call
    Screener open gets it at once; nothing of it goes on air."""
    from app.models.live_chat_message import LiveChatMessage
    await _show_or_404(db, show_id)
    text = " ".join((body.text or "").split())
    if not text:
        raise HTTPException(status_code=422, detail="Write something first")
    if len(text) > 500:
        raise HTTPException(status_code=422, detail="Too long: a chat line holds 500 characters")
    line = LiveChatMessage(id=uuid.uuid4(), live_show_id=show_id, user_id=user.id,
                           author=user.display_name or user.email, text=text,
                           # set here, to the microsecond: two lines in one second kept no order
                           created_at=datetime.now(timezone.utc))
    db.add(line)
    await db.commit()
    await db.refresh(line)
    row = _chat_row(line)
    await _say(str(show_id), "chat", row)
    return row


@router.get("/{show_id}/dumps/{dump_id}/audio")
async def dump_audio(
    show_id: uuid.UUID,
    dump_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """The recording of one Dump: what listeners heard just before it, a short gap,
    then what was cut. Staff only: this is the audio that was kept off the air."""
    from app.models.live_dump import LiveDump
    from app.services.storage_service import download_file
    d = await db.get(LiveDump, dump_id)
    if d is None or str(d.live_show_id) != str(show_id) or not d.file_path:
        raise NotFoundError("Dump not found")
    try:
        data = await download_file(d.file_path)
    except Exception:
        raise HTTPException(status_code=404, detail="The recording of this dump is not stored (yet). Try again in a moment.")
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})


@router.post("/{show_id}/delay")
async def set_delay(
    show_id: uuid.UUID,
    body: DelayRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
):
    """Change the delay during the show: `exit` winds it down to none (really live),
    `build` brings it back, `seconds` sets a new length for this show."""
    show = await _show_or_404(db, show_id)
    air = _air_of(show)
    if air is None:
        raise HTTPException(status_code=409, detail="The show is not on air yet: start broadcasting first")
    if body.action == "exit":
        air.exit_delay()
    else:
        air.enter_delay(body.seconds)
    logger.info("Live show %s: delay %s by %s (now steering to %.0f s)", show_id, body.action, user.email, air.target)
    status = air.status()
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "air_status", status)
    except Exception:
        pass
    return status


# --- Call management ---

async def _refuse_if_locked(db: AsyncSession, call_id) -> None:
    """End Call, Hold and Reject leave a locked call alone (409)."""
    call = await live_show_service.get_call(db, call_id)
    if call is not None and call.locked:
        raise HTTPException(status_code=409, detail="This call is locked. Unlock it first.")


async def _tell_web_caller(call_id, status: str) -> None:
    """A web caller's page and audio follow the screener (on air = mixed in)."""
    try:
        from app.api.v1.live_shows_ws import live_relay
        await live_relay.caller_status(str(call_id), status)
    except Exception as e:
        logger.warning("Could not update web caller %s: %s", call_id, e)


@router.get("/{show_id}/calls", response_model=CallInRequestListResponse)
async def list_calls(
    show_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """List all callers for a show."""
    calls = await live_show_service.get_show_calls(db, show_id)
    rows = await caller_history.with_history(db, calls)
    return CallInRequestListResponse(calls=rows, total=len(rows))


class CallerStanding(BaseModel):
    standing: Literal["normal", "priority", "blocked"] | None = None
    note: str | None = Field(default=None, max_length=500)


@router.put("/{show_id}/calls/{call_id}/caller")
async def set_caller_standing(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    body: CallerStanding,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
):
    """Mark the person behind a call as priority, blocked or normal, or note something
    about them. It holds for their later calls on every show. Blocking also takes
    their calls that wait in this show out of the queue (one on air stays: End Call
    and Dump are the tools there)."""
    call = await live_show_service.get_call(db, call_id)
    if not call or str(call.live_show_id) != str(show_id):
        raise NotFoundError("Call not found")
    if not call.caller_key:
        raise HTTPException(409, "This call does not say who is calling, so nothing can be kept about the caller.")
    profile = await caller_history.set_standing(
        db, call.caller_key, call.caller_name, body.standing, body.note, user.id)
    same = [c for c in await live_show_service.get_show_calls(db, show_id) if c.caller_key == call.caller_key]
    removed = []
    if body.standing == "blocked":
        for c in same:
            if c.status in (CallStatus.WAITING, CallStatus.SCREENING, CallStatus.APPROVED):
                await live_show_service.reject_call(db, c.id, screened_by=user.id)
                removed.append(c)
    await db.commit()
    h = await caller_history.histories(db, same)
    for c in removed:
        await _tell_web_caller(c.id, "rejected")
        await _say(show_id, "caller_removed", {"call_id": str(c.id), "reason": "blocked"})
    for c in same:
        if c not in removed:
            await _say(show_id, "caller_updated", {"call_id": str(c.id), "history": h.get(str(c.id))})
    return {"standing": profile.standing, "note": profile.note, "removed": len(removed)}


@router.post("/{show_id}/calls/{call_id}/approve", response_model=CallInRequestInDB)
async def approve_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_dj_or_manager),
):
    """Approve a caller."""
    call = await live_show_service.approve_call(db, call_id, screened_by=user.id)
    if not call:
        raise NotFoundError("Call not found")
    await _tell_web_caller(call_id, "approved")

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
    user: User = Depends(require_dj_or_manager),
):
    """Reject a caller."""
    await _refuse_if_locked(db, call_id)
    call = await live_show_service.reject_call(db, call_id, screened_by=user.id)
    if not call:
        raise NotFoundError("Call not found")
    await _tell_web_caller(call_id, "rejected")

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
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Push an approved caller on air. Up to four callers can be on air together (409 beyond that).
    While the show's delay is not full enough to Dump a caller, 409 as well unless `force`."""
    await _refuse_while_not_safe(db, show_id, force)
    call = await live_show_service.put_caller_on_air(db, show_id, call_id)
    if not call:
        raise NotFoundError("Call not found")
    await _tell_web_caller(call_id, "on_air")

    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), "caller_updated", {
            "call_id": str(call_id), "status": "on_air",
        })
    except Exception:
        pass

    return call


async def _refuse_while_not_safe(db: AsyncSession, show_id, force: bool) -> None:
    show = await live_show_service.get_show(db, show_id)
    air = _air_of(show) if show else None
    if air is not None and not force and air.configured > 0:
        status = air.status()
        if not status["safe"]:
            need = air.dump_size if 0 < air.dump_size < air.configured else air.configured
            raise HTTPException(status_code=409, detail={
                "code": "delay_not_safe",
                "message": (f"The delay is not safe yet: {air.buffered:.0f} s of {need:.0f} s. "
                            "A caller put on air now could not be fully dumped."),
            })


async def _say(show_id, event: str, data: dict) -> None:
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        await broadcast_show_event(str(show_id), event, data)
    except Exception:
        pass


@router.post("/{show_id}/calls/{call_id}/hold", response_model=CallInRequestInDB)
async def hold_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Take the caller on air off the air without hanging up (back to Approved). They
    stay connected, hear the show, and can be put on air again. 409 if not on air."""
    await _refuse_if_locked(db, call_id)
    call = await live_show_service.hold_caller(db, call_id)
    if not call or str(call.live_show_id) != str(show_id):
        raise NotFoundError("Call not found")
    await _tell_web_caller(call_id, "approved")
    await _say(show_id, "caller_updated", {"call_id": str(call_id), "status": "approved"})
    return call


# Next pressed twice (a double click, or the host and the screener together): the two
# requests both read "Avi is next" and both put him on air, or the second, a moment
# later, ended the caller the first had just put on and took the one after: a caller
# lost after a fraction of a second on air. One Next at a time per show, and a second
# one inside NEXT_REPEAT_SECONDS gets the first one's answer and changes nothing.
NEXT_REPEAT_SECONDS = 1.0
_next_locks: dict[str, asyncio.Lock] = {}
_next_last: dict[str, tuple[float, dict]] = {}


@router.post("/{show_id}/calls/next")
async def next_caller(
    show_id: uuid.UUID,
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """One press between callers (see `_next_caller`). Pressed twice within a second,
    the second press answers what the first did, with `"repeat": true`."""
    key = str(show_id)
    async with _next_locks.setdefault(key, asyncio.Lock()):
        last = _next_last.get(key)
        if last is not None and time.monotonic() - last[0] < NEXT_REPEAT_SECONDS:
            return {**last[1], "repeat": True}
        result = await _next_caller(show_id, force, db)
        # Written before the lock opens, so a press waiting behind this one reads it
        await db.commit()
        _next_last[key] = (time.monotonic(), result)
        if len(_next_last) > 200:
            for old in sorted(_next_last, key=lambda k: _next_last[k][0])[:100]:
                _next_last.pop(old, None)
                _next_locks.pop(old, None)
        return result


async def _next_caller(
    show_id: uuid.UUID,
    force: bool,
    db: AsyncSession,
) -> dict:
    """One press between callers: the call on air ends and the approved caller who
    has waited longest goes on air. With nobody approved it only ends the call on air;
    409 when there is neither. Like Put On Air, refused (409 delay_not_safe) while the
    delay could not cut the new caller, unless `force`; nothing is ended then."""
    from app.models.call_in_request import CallInRequest, CallStatus

    # Several callers can be on air together: Next ends them all, except a locked
    # call (a guest), which stays on air with the caller who comes
    on_air_now = (await db.execute(select(CallInRequest).where(
        CallInRequest.live_show_id == show_id, CallInRequest.status == CallStatus.ON_AIR,
        CallInRequest.locked == False,  # noqa: E712
    ).order_by(CallInRequest.air_start))).scalars().all()
    coming = await live_show_service.next_approved_call(db, show_id)
    if not on_air_now and coming is None:
        locked = (await db.execute(select(CallInRequest.id).where(
            CallInRequest.live_show_id == show_id, CallInRequest.status == CallStatus.ON_AIR,
            CallInRequest.locked == True,  # noqa: E712
        ).limit(1))).first()
        raise HTTPException(status_code=409, detail=(
            "The call on air is locked and no caller is approved" if locked
            else "No caller is on air and none is approved"))
    if coming is not None:
        await _refuse_while_not_safe(db, show_id, force)
    ended = None
    for current in on_air_now:
        done = await live_show_service.end_call(db, current.id)
        ended = ended or done
        await _tell_web_caller(current.id, "completed")
        await _say(show_id, "caller_removed", {"call_id": str(current.id), "reason": "ended"})
    on_air = None
    if coming is not None:
        on_air = await live_show_service.put_caller_on_air(db, show_id, coming.id)
        await _tell_web_caller(coming.id, "on_air")
        await _say(show_id, "caller_updated", {
            "call_id": str(coming.id), "status": "on_air",
            "air_start": on_air.air_start.isoformat() if on_air and on_air.air_start else None,
        })
    return {"ended": str(ended.id) if ended else None, "on_air": str(on_air.id) if on_air else None}


class LockRequest(BaseModel):
    locked: bool


@router.post("/{show_id}/calls/{call_id}/lock", response_model=CallInRequestInDB)
async def lock_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    body: LockRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """Lock or unlock a call. While locked, End Call, Hold, Reject and Next leave it
    alone. Dump with "drop the caller" still drops it, and the caller can hang up."""
    call = await live_show_service.get_call(db, call_id)
    if not call or str(call.live_show_id) != str(show_id):
        raise NotFoundError("Call not found")
    call.locked = body.locked
    await db.commit()
    await db.refresh(call)
    await _say(show_id, "caller_updated", {"call_id": str(call_id), "locked": call.locked})
    return call


@router.post("/{show_id}/calls/{call_id}/end-call", response_model=CallInRequestInDB)
async def end_active_call(
    show_id: uuid.UUID,
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_dj_or_manager),
):
    """End an active call."""
    await _refuse_if_locked(db, call_id)
    call = await live_show_service.end_call(db, call_id)
    if not call:
        raise NotFoundError("Call not found")
    await _tell_web_caller(call_id, "completed")

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
    _user: User = Depends(require_dj_or_manager),
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


# --- Public endpoint (no auth) ---

@router.get("/station/{station_id}/active")
async def get_active_show_for_station(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: get the active live show for a station (if any)."""
    result = await db.execute(
        select(LiveShow).where(
            LiveShow.station_id == station_id,
            LiveShow.status == LiveShowStatus.LIVE,
        ).limit(1)
    )
    show = result.scalar_one_or_none()
    # The station's stream address, for a page opened during the show: it used to
    # come only with the item on air, and during a show no item is on air, so the
    # Listen page had no stream to play until the show was over (2026-11-18)
    from app.config import settings as _settings
    stream_url = _settings.public_stream_url(station_id)
    if not show:
        return {"active": False, "show": None, "stream_url": stream_url}
    return {
        "active": True,
        "stream_url": stream_url,
        "show": {
            "id": str(show.id),
            "title": show.title,
            "description": show.description,
            "broadcast_mode": show.broadcast_mode.value if hasattr(show.broadcast_mode, 'value') else show.broadcast_mode,
            "icecast_mount": show.icecast_mount,
            "actual_start": show.actual_start.isoformat() if show.actual_start else None,
            "scheduled_end": show.scheduled_end.isoformat() if show.scheduled_end else None,
            "calls_enabled": show.calls_enabled,
        },
    }


# --- Twilio webhooks (no login; signed by Twilio with X-Twilio-Signature) ---

def twilio_signature(auth_token: str, url: str, params) -> str:
    """Twilio's request signature: base64 HMAC-SHA1 of the full URL followed by every
    POST parameter as name+value, sorted by name, keyed with the account's auth token."""
    import base64
    import hashlib
    import hmac

    data = url + "".join(f"{k}{v}" for k, v in sorted(params, key=lambda kv: kv[0]))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _twilio_urls(request: Request) -> list[str]:
    """The URL Twilio may have signed: behind Railway's proxy the app sees http, and
    Twilio was given BACKEND_PUBLIC_URL, so try those spellings too."""
    url = str(request.url)
    urls = [url]
    if url.startswith("http://"):
        urls.append("https://" + url[len("http://"):])
    if settings.BACKEND_PUBLIC_URL:
        tail = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        urls.append(settings.BACKEND_PUBLIC_URL.rstrip("/") + tail)
    return urls


async def require_twilio_signature(request: Request) -> None:
    """Refuse a webhook not signed by Twilio, so nobody can add fake callers to a show's
    queue or mark real callers as hung up. Checked whenever TWILIO_AUTH_TOKEN is set."""
    import hmac

    token = settings.TWILIO_AUTH_TOKEN
    if not token:
        return
    given = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    params = list(form.multi_items())
    if given and any(
        hmac.compare_digest(twilio_signature(token, url, params), given) for url in _twilio_urls(request)
    ):
        return
    logger.warning("Twilio webhook %s refused: bad or missing signature", request.url.path)
    raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def _phone_key(number) -> str:
    """A phone number as its numerals only, to compare +1 (555) 000-0002 with +15550000002."""
    return "".join(ch for ch in str(number or "") if ch in "0123456789")


async def _station_for_number(db: AsyncSession, number) -> uuid.UUID | None:
    """The station whose own call-in number was dialled, or None (shared number)."""
    from app.models.station import Station

    wanted = _phone_key(number)
    if not wanted:
        return None
    rows = (await db.execute(
        select(Station.id, Station.broadcast_config).where(Station.broadcast_config.isnot(None))
    )).all()
    for station_id, config in rows:
        if isinstance(config, dict) and _phone_key(config.get("call_in_number")) == wanted:
            return station_id
    return None


@router.post("/twilio/inbound", dependencies=[Depends(require_twilio_signature)])
async def twilio_inbound(request: Request, db: AsyncSession = Depends(get_db)):
    """Twilio inbound call webhook — find active show, create CallInRequest, return hold TwiML."""
    form = await request.form()
    caller_phone = form.get("From", "unknown")
    call_sid = form.get("CallSid", "")

    # Find an active live show with calls enabled: the dialled station's when the number
    # called (To) is a station's own call-in number (broadcast_config.call_in_number)
    query = select(LiveShow).where(
        LiveShow.status == LiveShowStatus.LIVE,
        LiveShow.calls_enabled == True,  # noqa: E712
    )
    dialled = await _station_for_number(db, form.get("To", ""))
    if dialled is not None:
        query = query.where(LiveShow.station_id == dialled)
    result = await db.execute(query.order_by(LiveShow.actual_start.desc().nullslast()).limit(1))
    show = result.scalar_one_or_none()

    if not show:
        return Response(content=generate_no_show_twiml(), media_type="application/xml")

    # Create call-in request
    now = datetime.now(timezone.utc)
    key = caller_history.phone_key(caller_phone)
    if await caller_history.standing_of(db, key) == "blocked":
        return Response(content=generate_no_show_twiml(), media_type="application/xml")
    call_request = CallInRequest(
        live_show_id=show.id,
        caller_phone=caller_phone,
        caller_key=key,
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
        # The whole call with its id (the screens key callers by id) and its history
        row = (await caller_history.with_history(db, [call_request]))[0]
        await broadcast_show_event(str(show.id), "caller_queued", {**row, "call_id": str(call_request.id)})
    except Exception:
        pass

    # The caller's sound joins the show's own mixer over a media stream. Without a
    # public https address for it, the old hold line (nobody hears that caller on air)
    from app.services.call_in import call_token
    from app.services.twilio_voice_service import generate_stream_twiml, phone_stream_url
    stream_url = phone_stream_url(settings.BACKEND_PUBLIC_URL, str(call_request.id))
    if stream_url:
        twiml = generate_stream_twiml(stream_url, call_token(call_request.id))
    else:
        logger.warning("Phone call %s: BACKEND_PUBLIC_URL is not an https address, caller cannot go on air",
                       call_request.id)
        twiml = generate_hold_twiml(str(show.id))
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/status", dependencies=[Depends(require_twilio_signature)])
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
            if call.status == CallStatus.ON_AIR:
                # Hung up while on air: the call happened, it just ended
                call.status = CallStatus.COMPLETED
                call.air_end = datetime.now(timezone.utc)
            else:
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
