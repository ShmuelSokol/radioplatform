"""
Billing & Stripe integration endpoints.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_db, require_manager, require_sponsor, require_sponsor_or_manager
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.invoice import Invoice, InvoiceStatus
from app.models.sponsor import Sponsor
from app.models.user import User, UserRole
from app.schemas.billing import BillingSummary, CheckoutResponse, InvoiceCreate, InvoiceInDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


async def _get_sponsor_for_user(db: AsyncSession, user: User) -> Sponsor | None:
    result = await db.execute(select(Sponsor).where(Sponsor.user_id == user.id))
    return result.scalar_one_or_none()


@router.get("/invoices", response_model=list[InvoiceInDB])
async def list_invoices(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    from app.models.ad_campaign import AdCampaign

    stmt = (
        select(Invoice, Sponsor.name.label("sponsor_name"), AdCampaign.name.label("campaign_name"))
        .outerjoin(Sponsor, Invoice.sponsor_id == Sponsor.id)
        .outerjoin(AdCampaign, Invoice.campaign_id == AdCampaign.id)
        .order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
    )

    if user.role == UserRole.SPONSOR:
        sponsor = await _get_sponsor_for_user(db, user)
        if not sponsor:
            return []
        stmt = stmt.where(Invoice.sponsor_id == sponsor.id)

    result = await db.execute(stmt)
    invoices = []
    for row in result.all():
        invoice = row[0]
        invoice.sponsor_name = row[1]
        invoice.campaign_name = row[2]
        invoices.append(invoice)
    return invoices


@router.post("/invoices", response_model=InvoiceInDB, status_code=201)
async def create_invoice(
    data: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    invoice = Invoice(
        sponsor_id=data.sponsor_id,
        campaign_id=data.campaign_id,
        amount_cents=data.amount_cents,
        currency=data.currency,
        due_date=data.due_date,
        description=data.description,
        status=InvoiceStatus.SENT,
    )
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice)

    # Send email notification to sponsor
    try:
        from app.services.email_service import send_invoice_created
        sponsor = invoice.sponsor
        if sponsor and sponsor.contact_email:
            due = str(data.due_date) if data.due_date else None
            await send_invoice_created(sponsor.contact_email, data.amount_cents, due)
    except Exception as e:
        logger.warning(f"Failed to send invoice email: {e}")

    return invoice


@router.post("/invoices/{invoice_id}/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    invoice_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    if not settings.stripe_enabled:
        raise BadRequestError("Stripe is not configured")

    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise NotFoundError("Invoice not found")

    sponsor = await _get_sponsor_for_user(db, user)
    if not sponsor or invoice.sponsor_id != sponsor.id:
        raise NotFoundError("Invoice not found")

    if invoice.status == InvoiceStatus.PAID:
        raise BadRequestError("Invoice is already paid")

    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": invoice.currency.lower(),
                "unit_amount": invoice.amount_cents,
                "product_data": {
                    "name": invoice.description or f"Invoice {str(invoice.id)[:8]}",
                },
            },
            "quantity": 1,
        }],
        mode="payment",
        metadata={"invoice_id": str(invoice.id)},
        success_url=f"{settings.CORS_ORIGINS[0]}/sponsor/billing?payment=success" if settings.CORS_ORIGINS else "http://localhost:3000/sponsor/billing?payment=success",
        cancel_url=f"{settings.CORS_ORIGINS[0]}/sponsor/billing?payment=cancelled" if settings.CORS_ORIGINS else "http://localhost:3000/sponsor/billing?payment=cancelled",
    )

    return CheckoutResponse(checkout_url=session.url)


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.stripe_enabled:
        raise BadRequestError("Stripe is not configured")

    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise BadRequestError("Invalid webhook signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        invoice_id = session.get("metadata", {}).get("invoice_id")
        if invoice_id:
            result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
            invoice = result.scalar_one_or_none()
            if invoice:
                invoice.status = InvoiceStatus.PAID
                invoice.paid_at = datetime.now(timezone.utc)
                invoice.stripe_payment_intent_id = session.get("payment_intent")
                await db.commit()
                logger.info(f"Invoice {invoice_id} marked as paid")

                # Send payment confirmation email
                try:
                    from app.services.email_service import send_payment_confirmation
                    sponsor = invoice.sponsor
                    if sponsor and sponsor.contact_email:
                        await send_payment_confirmation(sponsor.contact_email, invoice.amount_cents)
                except Exception as e:
                    logger.warning(f"Failed to send payment confirmation email: {e}")

    return {"status": "ok"}


@router.get("/summary", response_model=BillingSummary)
async def get_billing_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    if not sponsor:
        return BillingSummary(total_owed_cents=0, total_paid_cents=0, pending_invoices=0)

    base = [Invoice.sponsor_id == sponsor.id]

    # Total owed (sent + overdue)
    owed_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.amount_cents), 0)).where(
            *base, Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE])
        )
    )
    total_owed = owed_result.scalar() or 0

    # Total paid
    paid_result = await db.execute(
        select(func.coalesce(func.sum(Invoice.amount_cents), 0)).where(
            *base, Invoice.status == InvoiceStatus.PAID
        )
    )
    total_paid = paid_result.scalar() or 0

    # Pending count
    pending_result = await db.execute(
        select(func.count(Invoice.id)).where(
            *base, Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE])
        )
    )
    pending_count = pending_result.scalar() or 0

    return BillingSummary(
        total_owed_cents=total_owed,
        total_paid_cents=total_paid,
        pending_invoices=pending_count,
    )
