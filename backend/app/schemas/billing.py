"""Pydantic schemas for Billing / Invoices."""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class InvoiceCreate(BaseModel):
    sponsor_id: UUID | str
    campaign_id: UUID | str | None = None
    amount_cents: int
    currency: str = "USD"
    due_date: date | None = None
    description: str | None = None


class InvoiceInDB(BaseModel):
    id: UUID | str
    sponsor_id: UUID | str
    sponsor_name: str | None = None
    campaign_id: UUID | str | None = None
    campaign_name: str | None = None
    amount_cents: int
    currency: str
    status: str
    stripe_invoice_id: str | None = None
    stripe_payment_intent_id: str | None = None
    due_date: date | None = None
    paid_at: datetime | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BillingSummary(BaseModel):
    total_owed_cents: int
    total_paid_cents: int
    pending_invoices: int


class CheckoutResponse(BaseModel):
    checkout_url: str
