"""Web call-ins: a listener calls the live show from a page with their microphone
(no phone line). See wiki/live-shows.md.

A call is an ordinary CallInRequest (caller_phone "Web caller"), so the screener
and host console handle it like a phone caller. The caller's page proves which call
it is with a token derived from the call id and the server secret; nothing new is
stored."""
import asyncio
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

WEB_CALLER_PHONE = "Web caller"
# A caller whose page drops (a reload, a tunnel) has this long to come back before
# the call is taken off the screener
RECONNECT_GRACE_SECONDS = 15.0


def call_token(call_id: str | uuid.UUID) -> str:
    key = settings.JWT_SECRET_KEY.encode()
    return hmac.new(key, f"web-call:{call_id}".encode(), hashlib.sha256).hexdigest()


def call_token_ok(call_id: str, token: str) -> bool:
    try:
        uuid.UUID(call_id)
    except ValueError:
        return False
    return bool(token) and hmac.compare_digest(call_token(call_id), token)


async def caller_left(call_id: str, show_id: str) -> None:
    """The caller's socket closed: unless they come back soon, end the call."""
    asyncio.get_running_loop().create_task(_end_if_gone(call_id, show_id, RECONNECT_GRACE_SECONDS))


async def _end_if_gone(call_id: str, show_id: str, wait: float) -> None:
    from app.api.v1.live_shows_ws import broadcast_show_event, live_relay
    from app.db.session import get_async_session
    from app.models.call_in_request import CallInRequest, CallStatus

    await asyncio.sleep(wait)
    if call_id in live_relay.callers:
        return  # reconnected
    try:
        async for db in get_async_session():
            call = await db.get(CallInRequest, uuid.UUID(call_id))
            if call is None:
                return
            status = call.status.value if hasattr(call.status, "value") else str(call.status)
            if status in ("waiting", "screening", "approved"):
                call.status = CallStatus.ABANDONED
            elif status == "on_air":
                call.status = CallStatus.COMPLETED
                call.air_end = datetime.now(timezone.utc)
            else:
                return
            await db.commit()
            break
        await broadcast_show_event(show_id, "caller_removed", {"call_id": call_id, "reason": "hung_up"})
        logger.info("Web caller %s left (show %s)", call_id, show_id)
    except Exception as e:
        logger.error("Could not close web call %s: %s", call_id, e)
