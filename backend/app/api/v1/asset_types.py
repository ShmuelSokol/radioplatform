"""
Asset type management endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.asset_type import AssetTypeModel
from app.schemas.asset_type import AssetTypeCreate, AssetTypeInDB, AssetTypeUpdate

router = APIRouter(prefix="/asset-types", tags=["asset-types"])


@router.get("", response_model=list[AssetTypeInDB])
async def list_asset_types(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(AssetTypeModel).order_by(AssetTypeModel.name)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=AssetTypeInDB, status_code=201)
async def create_asset_type(
    data: AssetTypeCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = AssetTypeModel(name=data.name)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.patch("/{asset_type_id}", response_model=AssetTypeInDB)
async def update_asset_type(
    asset_type_id: UUID,
    data: AssetTypeUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(AssetTypeModel).where(AssetTypeModel.id == asset_type_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Asset type not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{asset_type_id}", status_code=204)
async def delete_asset_type(
    asset_type_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(AssetTypeModel).where(AssetTypeModel.id == asset_type_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Asset type not found")

    await db.delete(record)
    await db.commit()
