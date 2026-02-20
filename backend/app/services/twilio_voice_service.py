"""
Twilio Voice service for live call-in handling.
No-op when twilio_voice_enabled is False.
"""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.call_in_request import CallInRequest, CallStatus

logger = logging.getLogger(__name__)


def generate_hold_twiml(show_id: str) -> str:
    """Generate TwiML XML that puts a caller on hold with music."""
    hold_music_url = settings.LIVE_SHOW_HOLD_MUSIC_URL or "http://com.twilio.music.classical.s3.amazonaws.com/BusssyBoy.mp3"
    conference_name = f"hold-{show_id}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Thank you for calling in. Please hold while we connect you.</Say>
    <Dial>
        <Conference
            waitUrl="{hold_music_url}"
            waitMethod="GET"
            startConferenceOnEnter="false"
            endConferenceOnExit="false"
            statusCallback="{settings.BACKEND_PUBLIC_URL}/api/v1/live-shows/twilio/status"
            statusCallbackEvent="join leave"
        >{conference_name}</Conference>
    </Dial>
</Response>"""


def generate_no_show_twiml() -> str:
    """Generate TwiML for when there is no active show accepting calls."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, there is no live show accepting calls at this time. Please try again later.</Say>
    <Hangup/>
</Response>"""


async def move_caller_to_conference(call_sid: str | None, conference_name: str) -> bool:
    """Redirect a caller from hold conference to the live conference."""
    if not call_sid or not settings.twilio_voice_enabled:
        logger.debug("Twilio voice not enabled — move caller skipped")
        return False

    try:
        from twilio.rest import Client
        from twilio.twiml.voice_response import VoiceResponse, Dial

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

        # Build TwiML for live conference
        response = VoiceResponse()
        dial = Dial()
        dial.conference(
            conference_name,
            start_conference_on_enter=True,
            end_conference_on_exit=False,
        )
        response.append(dial)

        # Redirect the call
        client.calls(call_sid).update(twiml=str(response))
        logger.info("Caller %s moved to conference %s", call_sid, conference_name)
        return True
    except Exception as e:
        logger.error("Failed to move caller %s to conference: %s", call_sid, e)
        return False


async def end_caller(call_sid: str | None) -> bool:
    """Hang up a single call."""
    if not call_sid or not settings.twilio_voice_enabled:
        logger.debug("Twilio voice not enabled — end caller skipped")
        return False

    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.calls(call_sid).update(status="completed")
        logger.info("Ended call %s", call_sid)
        return True
    except Exception as e:
        logger.error("Failed to end call %s: %s", call_sid, e)
        return False


async def end_all_calls_for_show(db: AsyncSession, show_id: UUID | str) -> int:
    """Hang up all active calls for a show. Returns count of ended calls."""
    active_statuses = [
        CallStatus.WAITING,
        CallStatus.SCREENING,
        CallStatus.APPROVED,
        CallStatus.ON_AIR,
    ]
    result = await db.execute(
        select(CallInRequest).where(
            CallInRequest.live_show_id == show_id,
            CallInRequest.status.in_(active_statuses),
        )
    )
    calls = result.scalars().all()

    ended = 0
    for call in calls:
        await end_caller(call.twilio_call_sid)
        call.status = CallStatus.COMPLETED
        ended += 1

    if ended:
        await db.flush()
        logger.info("Ended %d calls for show %s", ended, show_id)
    return ended
