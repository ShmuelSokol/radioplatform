"""
Ad Campaign management endpoints.
Sponsors see their own campaigns; managers see all.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager, require_sponsor_or_manager
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.ad_campaign import AdCampaign, AdComment, AdDraft, CampaignStatus
from app.models.sponsor import Sponsor
from app.models.user import User, UserRole
from app.schemas.ad_campaign import (
    CampaignCreate,
    CampaignInDB,
    CampaignStatusUpdate,
    CampaignUpdate,
    CommentCreate,
    CommentInDB,
    DraftCreate,
    DraftInDB,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


async def _get_sponsor_for_user(db: AsyncSession, user: User) -> Sponsor | None:
    """Get sponsor record linked to user (for sponsor-role users)."""
    result = await db.execute(select(Sponsor).where(Sponsor.user_id == user.id))
    return result.scalar_one_or_none()


def _can_access_campaign(user: User, campaign: AdCampaign, sponsor: Sponsor | None) -> bool:
    """Check if user can access this campaign."""
    if user.role in (UserRole.ADMIN, UserRole.MANAGER):
        return True
    if sponsor and campaign.sponsor_id == sponsor.id:
        return True
    return False


# --- Campaigns CRUD ---

@router.get("", response_model=list[CampaignInDB])
async def list_campaigns(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    from app.models.sponsor import Sponsor

    stmt = (
        select(AdCampaign, Sponsor.name.label("sponsor_name"))
        .outerjoin(Sponsor, AdCampaign.sponsor_id == Sponsor.id)
        .order_by(AdCampaign.created_at.desc()).offset(skip).limit(limit)
    )

    if user.role == UserRole.SPONSOR:
        sponsor = await _get_sponsor_for_user(db, user)
        if not sponsor:
            return []
        stmt = stmt.where(AdCampaign.sponsor_id == sponsor.id)

    result = await db.execute(stmt)
    campaigns = []
    for row in result.all():
        campaign = row[0]
        campaign.sponsor_name = row[1]
        campaigns.append(campaign)
    return campaigns


@router.post("", response_model=CampaignInDB, status_code=201)
async def create_campaign(
    data: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    sponsor = await _get_sponsor_for_user(db, user)
    if user.role == UserRole.SPONSOR and not sponsor:
        raise ForbiddenError("No sponsor record linked to your account")

    # For sponsors, always use their own sponsor_id
    sponsor_id = sponsor.id if sponsor else None
    if not sponsor_id:
        # Manager creating — need to pick a sponsor; use first one for now
        raise ForbiddenError("Managers must specify a sponsor_id when creating campaigns via the admin panel")

    campaign = AdCampaign(
        sponsor_id=sponsor_id,
        name=data.name,
        description=data.description,
        start_date=data.start_date,
        end_date=data.end_date,
        budget_cents=data.budget_cents,
        target_rules=data.target_rules,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.get("/{campaign_id}", response_model=CampaignInDB)
async def get_campaign(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    return campaign


@router.patch("/{campaign_id}", response_model=CampaignInDB)
async def update_campaign(
    campaign_id: UUID,
    data: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(campaign, key, value)

    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.patch("/{campaign_id}/status", response_model=CampaignInDB)
async def update_campaign_status(
    campaign_id: UUID,
    data: CampaignStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    campaign.status = CampaignStatus(data.status)
    await db.commit()
    await db.refresh(campaign)

    # Send email notification to sponsor
    try:
        from app.services.email_service import send_campaign_status_update
        sponsor = campaign.sponsor
        if sponsor and sponsor.contact_email:
            await send_campaign_status_update(sponsor.contact_email, campaign.name, data.status)
    except Exception as e:
        logger.warning(f"Failed to send campaign status email: {e}")

    return campaign


# --- Drafts ---

@router.get("/{campaign_id}/drafts", response_model=list[DraftInDB])
async def list_drafts(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    # Verify campaign access
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    stmt = select(AdDraft).where(AdDraft.campaign_id == campaign_id).order_by(AdDraft.version.desc())
    result = await db.execute(stmt)
    drafts = result.scalars().all()

    return [
        DraftInDB(
            id=d.id,
            campaign_id=d.campaign_id,
            version=d.version,
            script_text=d.script_text,
            audio_file_path=d.audio_file_path,
            notes=d.notes,
            created_by=d.created_by,
            created_at=d.created_at,
            updated_at=d.updated_at,
            user_email=d.creator.email if d.creator else None,
            user_display_name=d.creator.display_name if d.creator else None,
        )
        for d in drafts
    ]


@router.post("/{campaign_id}/drafts", response_model=DraftInDB, status_code=201)
async def create_draft(
    campaign_id: UUID,
    data: DraftCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    # Get next version number
    max_version_result = await db.execute(
        select(func.coalesce(func.max(AdDraft.version), 0)).where(AdDraft.campaign_id == campaign_id)
    )
    next_version = (max_version_result.scalar() or 0) + 1

    draft = AdDraft(
        campaign_id=campaign_id,
        version=next_version,
        script_text=data.script_text,
        notes=data.notes,
        created_by=user.id,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    return DraftInDB(
        id=draft.id,
        campaign_id=draft.campaign_id,
        version=draft.version,
        script_text=draft.script_text,
        audio_file_path=draft.audio_file_path,
        notes=draft.notes,
        created_by=draft.created_by,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        user_email=user.email,
        user_display_name=user.display_name,
    )


@router.post("/{campaign_id}/drafts/{draft_id}/upload-audio")
async def upload_draft_audio(
    campaign_id: UUID,
    draft_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    draft_result = await db.execute(
        select(AdDraft).where(AdDraft.id == draft_id, AdDraft.campaign_id == campaign_id)
    )
    draft = draft_result.scalar_one_or_none()
    if not draft:
        raise NotFoundError("Draft not found")

    # Store via Supabase storage if available, otherwise note the filename
    try:
        from app.services.supabase_storage_service import supabase_storage
        from app.config import settings

        if settings.supabase_storage_enabled:
            file_bytes = await file.read()
            path = f"ad-drafts/{campaign_id}/{draft_id}/{file.filename}"
            url = await supabase_storage.upload(path, file_bytes, file.content_type or "audio/mpeg")
            draft.audio_file_path = url
        else:
            draft.audio_file_path = f"ad-drafts/{campaign_id}/{draft_id}/{file.filename}"
    except Exception:
        draft.audio_file_path = f"ad-drafts/{campaign_id}/{draft_id}/{file.filename}"

    await db.commit()
    await db.refresh(draft)
    return {"audio_file_path": draft.audio_file_path}


# --- Comments ---

@router.get("/{campaign_id}/comments", response_model=list[CommentInDB])
async def list_comments(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    stmt = select(AdComment).where(AdComment.campaign_id == campaign_id).order_by(AdComment.created_at.asc())
    result = await db.execute(stmt)
    comments = result.scalars().all()

    return [
        CommentInDB(
            id=c.id,
            campaign_id=c.campaign_id,
            draft_id=c.draft_id,
            user_id=c.user_id,
            body=c.body,
            created_at=c.created_at,
            user_email=c.user.email if c.user else None,
            user_display_name=c.user.display_name if c.user else None,
        )
        for c in comments
    ]


@router.post("/{campaign_id}/comments", response_model=CommentInDB, status_code=201)
async def create_comment(
    campaign_id: UUID,
    data: CommentCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor_or_manager),
):
    result = await db.execute(select(AdCampaign).where(AdCampaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise NotFoundError("Campaign not found")

    sponsor = await _get_sponsor_for_user(db, user) if user.role == UserRole.SPONSOR else None
    if not _can_access_campaign(user, campaign, sponsor):
        raise ForbiddenError("Access denied")

    comment = AdComment(
        campaign_id=campaign_id,
        draft_id=data.draft_id,
        user_id=user.id,
        body=data.body,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    # Send email notification to sponsor (if comment is from a manager)
    try:
        if user.role in (UserRole.ADMIN, UserRole.MANAGER):
            from app.services.email_service import send_new_comment_notification
            sponsor = campaign.sponsor
            if sponsor and sponsor.contact_email:
                commenter = user.display_name or user.email
                preview = data.body[:200]
                await send_new_comment_notification(
                    sponsor.contact_email, campaign.name, commenter, preview
                )
    except Exception as e:
        logger.warning(f"Failed to send comment notification email: {e}")

    return CommentInDB(
        id=comment.id,
        campaign_id=comment.campaign_id,
        draft_id=comment.draft_id,
        user_id=comment.user_id,
        body=comment.body,
        created_at=comment.created_at,
        user_email=user.email,
        user_display_name=user.display_name,
    )
