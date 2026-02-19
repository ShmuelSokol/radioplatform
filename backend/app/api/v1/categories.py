"""
Category management endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryInDB, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryInDB])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Category).order_by(Category.name)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=CategoryInDB, status_code=201)
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = Category(name=data.name)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.patch("/{category_id}", response_model=CategoryInDB)
async def update_category(
    category_id: UUID,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Category).where(Category.id == category_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Category not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Category).where(Category.id == category_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Category not found")

    await db.delete(record)
    await db.commit()
