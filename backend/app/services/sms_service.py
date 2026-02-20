"""
SMS and WhatsApp notification service via Twilio.
No-op when TWILIO_ACCOUNT_SID is not configured.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def send_sms(to: str, body: str) -> bool:
    """Send an SMS message via Twilio. Returns True on success."""
    if not settings.twilio_enabled:
        logger.debug("Twilio not configured — SMS skipped to %s", to)
        return False

    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to,
        )
        logger.info("SMS sent to %s", to)
        return True
    except Exception as e:
        logger.error("Failed to send SMS to %s: %s", to, e)
        return False


async def send_whatsapp(to: str, body: str) -> bool:
    """Send a WhatsApp message via Twilio. Returns True on success."""
    if not settings.twilio_enabled:
        logger.debug("Twilio not configured — WhatsApp skipped to %s", to)
        return False

    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=body,
            from_=f"whatsapp:{settings.TWILIO_PHONE_NUMBER}",
            to=f"whatsapp:{to}",
        )
        logger.info("WhatsApp sent to %s", to)
        return True
    except Exception as e:
        logger.error("Failed to send WhatsApp to %s: %s", to, e)
        return False
