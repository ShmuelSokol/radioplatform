"""
AI email drafting & sending endpoints.
Managers can generate AI-drafted emails and send them to sponsors.
"""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.user import User
from app.services.ai_email_service import draft_email
from app.services.email_service import send_email

router = APIRouter(prefix="/ai-emails", tags=["ai-emails"])


class DraftRequest(BaseModel):
    sponsor_name: str
    purpose: str = "general"  # welcome, renewal, follow_up, custom
    campaign_name: str | None = None
    custom_instructions: str | None = None
    recent_stats: dict[str, Any] | None = None


class DraftResponse(BaseModel):
    draft_text: str


class SendRequest(BaseModel):
    to: str
    subject: str
    body: str  # The final (possibly edited) email text


class SendResponse(BaseModel):
    success: bool
    message: str


@router.post("/draft", response_model=DraftResponse)
async def generate_draft(
    data: DraftRequest,
    _=Depends(require_manager),
):
    context = data.model_dump(exclude_none=True)
    text = await draft_email(context)
    return DraftResponse(draft_text=text)


@router.post("/send", response_model=SendResponse)
async def send_drafted_email(
    data: SendRequest,
    _=Depends(require_manager),
):
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        {data.body.replace(chr(10), '<br />')}
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
        <p style="color: #9ca3af; font-size: 12px;">Kol Bramah Radio</p>
    </div>
    """

    success = await send_email(data.to, data.subject, html_body)
    if success:
        return SendResponse(success=True, message="Email sent successfully")
    else:
        return SendResponse(success=False, message="Email sending failed or Resend is not configured")
