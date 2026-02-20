"""
Transactional email service using Resend.
All sends are no-ops when RESEND_API_KEY is not configured.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send a transactional email via Resend. Returns True on success."""
    if not settings.resend_enabled:
        logger.info(f"Email skipped (Resend not configured): to={to}, subject={subject}")
        return False

    import resend
    resend.api_key = settings.RESEND_API_KEY

    try:
        resend.Emails.send({
            "from": settings.RESEND_FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html_body,
        })
        logger.info(f"Email sent: to={to}, subject={subject}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


async def send_campaign_status_update(
    sponsor_email: str, campaign_name: str, new_status: str
) -> bool:
    subject = f"Campaign Update: {campaign_name}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #4f46e5;">Campaign Status Update</h2>
        <p>Your campaign <strong>{campaign_name}</strong> has been updated to:
        <strong style="color: #4f46e5;">{new_status.replace('_', ' ').title()}</strong></p>
        <p>Log in to your <a href="https://studio-kolbramah-radio.vercel.app/sponsor/dashboard"
        style="color: #4f46e5;">Sponsor Portal</a> for details.</p>
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
        <p style="color: #9ca3af; font-size: 12px;">Kol Bramah Radio</p>
    </div>
    """
    return await send_email(sponsor_email, subject, html)


async def send_new_comment_notification(
    sponsor_email: str, campaign_name: str, commenter_name: str, comment_preview: str
) -> bool:
    subject = f"New Comment on {campaign_name}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #4f46e5;">New Comment</h2>
        <p><strong>{commenter_name}</strong> commented on <strong>{campaign_name}</strong>:</p>
        <blockquote style="border-left: 3px solid #4f46e5; padding-left: 12px; color: #6b7280;">
            {comment_preview}
        </blockquote>
        <p><a href="https://studio-kolbramah-radio.vercel.app/sponsor/dashboard"
        style="color: #4f46e5;">View in Portal</a></p>
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
        <p style="color: #9ca3af; font-size: 12px;">Kol Bramah Radio</p>
    </div>
    """
    return await send_email(sponsor_email, subject, html)


async def send_invoice_created(
    sponsor_email: str, invoice_amount_cents: int, due_date: str | None
) -> bool:
    amount = f"${invoice_amount_cents / 100:.2f}"
    due = due_date or "upon receipt"
    subject = f"New Invoice: {amount}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #4f46e5;">New Invoice</h2>
        <p>You have a new invoice for <strong>{amount}</strong>, due <strong>{due}</strong>.</p>
        <p><a href="https://studio-kolbramah-radio.vercel.app/sponsor/billing"
        style="color: #4f46e5; text-decoration: none; background: #4f46e5; color: white; padding: 10px 20px; border-radius: 6px; display: inline-block;">
        View & Pay</a></p>
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
        <p style="color: #9ca3af; font-size: 12px;">Kol Bramah Radio</p>
    </div>
    """
    return await send_email(sponsor_email, subject, html)


async def send_payment_confirmation(
    sponsor_email: str, invoice_amount_cents: int
) -> bool:
    amount = f"${invoice_amount_cents / 100:.2f}"
    subject = f"Payment Confirmed: {amount}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #16a34a;">Payment Confirmed</h2>
        <p>Thank you! Your payment of <strong>{amount}</strong> has been received.</p>
        <p><a href="https://studio-kolbramah-radio.vercel.app/sponsor/billing"
        style="color: #4f46e5;">View Billing History</a></p>
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
        <p style="color: #9ca3af; font-size: 12px;">Kol Bramah Radio</p>
    </div>
    """
    return await send_email(sponsor_email, subject, html)
