"""
Sponsorship wording rules (viewable by sponsors, editable by managers) and
the copy review that checks a sponsor's proposed wording against them.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import limiter
from app.core.dependencies import get_db, require_manager, require_sponsor_or_manager
from app.models.sponsorship_rules import SponsorshipRules
from app.models.user import User
from app.schemas.copy_review import (
    CopyReviewRequest,
    CopyReviewResponse,
    RulesOut,
    RulesUpdate,
)
from app.services.copy_review_service import get_current_rules, review_copy, save_rules

router = APIRouter(prefix="/sponsorship", tags=["sponsorship"])


def _out(row: SponsorshipRules) -> RulesOut:
    return RulesOut(
        version=row.version,
        rules_text=row.rules_text,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


@router.get("/rules", response_model=RulesOut)
async def get_rules(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_sponsor_or_manager),
):
    version, text, row = await get_current_rules(db)
    if row is None:
        return RulesOut(version=version, rules_text=text, is_default=True)
    return _out(row)


@router.put("/rules", response_model=RulesOut)
async def update_rules(
    data: RulesUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Save edited rules as a new version. Reviews use it immediately."""
    row = await save_rules(db, data.rules_text, user.id)
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.get("/rules/history", response_model=list[RulesOut])
async def rules_history(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    result = await db.execute(
        select(SponsorshipRules).order_by(SponsorshipRules.version.desc()).limit(50)
    )
    return [_out(r) for r in result.scalars().all()]


@router.post("/copy-review", response_model=CopyReviewResponse)
@limiter.limit("12/minute")
async def copy_review(
    request: Request,
    data: CopyReviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_sponsor_or_manager),
):
    """Check proposed sponsorship wording against the rules in force."""
    return await review_copy(db, data.script_text, data.sponsor_name)
