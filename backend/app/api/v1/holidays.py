"""
Holiday/blackout window management endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.holiday_window import HolidayWindow
from app.schemas.holiday import (
    HolidayWindowCreate,
    HolidayWindowInDB,
    HolidayWindowUpdate,
)

router = APIRouter(prefix="/holidays", tags=["holidays"])


@router.get("", response_model=list[HolidayWindowInDB])
async def list_holidays(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(HolidayWindow).offset(skip).limit(limit).order_by(HolidayWindow.start_datetime)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=HolidayWindowInDB, status_code=201)
async def create_holiday(
    data: HolidayWindowCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = HolidayWindow(**data.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.put("/{holiday_id}", response_model=HolidayWindowInDB)
async def update_holiday(
    holiday_id: UUID,
    data: HolidayWindowUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(HolidayWindow).where(HolidayWindow.id == holiday_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Holiday window not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{holiday_id}", status_code=204)
async def delete_holiday(
    holiday_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(HolidayWindow).where(HolidayWindow.id == holiday_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Holiday window not found")

    await db.delete(record)
    await db.commit()
