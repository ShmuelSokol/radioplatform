"""Public web call-ins: a listener calls the station's live show from their browser.
See app/services/call_in.py and wiki/live-shows.md."""
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from app.schemas.live_show import CallInRequestInDB
from app.services.call_in import WEB_CALLER_PHONE, call_token

router = APIRouter(prefix="/call-in", tags=["call-in"])

RATE_WINDOW_SECONDS = 600
RATE_MAX = 5  # calls from one address in ten minutes
_recent: dict[str, deque] = {}


def _rate_limited(key: str) -> bool:
    now = time.time()
    q = _recent.setdefault(key, deque())
    while q and q[0] < now - RATE_WINDOW_SECONDS:
        q.popleft()
    if len(q) >= RATE_MAX:
        return True
    q.append(now)
    return False


class WebCallIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=300)


@router.post("/{station_id}", status_code=201)
async def start_web_call(station_id: uuid.UUID, body: WebCallIn, request: Request,
                         db: AsyncSession = Depends(get_db)):
    """Join the queue of the station's live show. Returns the call and the token its
    page uses on /ws/live/call/{call_id}."""
    ip = request.client.host if request.client else "?"
    if _rate_limited(request.headers.get("x-forwarded-for", ip).split(",")[0].strip()):
        raise HTTPException(429, "Too many calls in a few minutes. Please wait a little and try again.")
    show = (await db.execute(
        select(LiveShow).where(LiveShow.station_id == station_id, LiveShow.status == LiveShowStatus.LIVE).limit(1)
    )).scalar_one_or_none()
    if show is None:
        raise HTTPException(409, "There is no live show on this station right now.")
    if not show.calls_enabled:
        raise HTTPException(409, "This show is not taking calls.")
    now = datetime.now(timezone.utc)
    call = CallInRequest(
        id=uuid.uuid4(), live_show_id=show.id, caller_phone=WEB_CALLER_PHONE,
        caller_name=body.name.strip(), notes=(body.note or "").strip() or None,
        status=CallStatus.WAITING, hold_start=now,
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)
    data = CallInRequestInDB.model_validate(call).model_dump(mode="json")
    try:
        from app.api.v1.live_shows_ws import broadcast_show_event
        # The full call, so the screener can list it at once (it keys callers by id)
        await broadcast_show_event(str(show.id), "caller_queued", {**data, "call_id": str(call.id)})
    except Exception:
        pass
    return {"call_id": str(call.id), "token": call_token(call.id), "show_id": str(show.id),
            "show_title": show.title, "status": "waiting"}
