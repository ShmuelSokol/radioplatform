"""
Sponsor/ad management endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.sponsor import Sponsor
from app.schemas.sponsor import SponsorCreate, SponsorInDB, SponsorUpdate

router = APIRouter(prefix="/sponsors", tags=["sponsors"])


@router.get("", response_model=list[SponsorInDB])
async def list_sponsors(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Sponsor).offset(skip).limit(limit).order_by(Sponsor.priority.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=SponsorInDB, status_code=201)
async def create_sponsor(
    data: SponsorCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = Sponsor(**data.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.put("/{sponsor_id}", response_model=SponsorInDB)
async def update_sponsor(
    sponsor_id: UUID,
    data: SponsorUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Sponsor).where(Sponsor.id == sponsor_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Sponsor not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{sponsor_id}", status_code=204)
async def delete_sponsor(
    sponsor_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Sponsor).where(Sponsor.id == sponsor_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Sponsor not found")

    await db.delete(record)
    await db.commit()
