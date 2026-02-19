"""
Playlist template CRUD endpoints — manage rotation patterns of asset types/categories.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, require_manager
from app.models.asset import Asset
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.schemas.playlist_template import (
    PlaylistTemplateCreate,
    PlaylistTemplateInDB,
    PlaylistTemplateUpdate,
    TemplateSlotCreate,
    TemplateSlotInDB,
    TemplateSlotUpdate,
)

router = APIRouter(prefix="/playlists", tags=["playlists"])


# ==================== Templates ====================

@router.get("", response_model=list[PlaylistTemplateInDB])
async def list_templates(
    station_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(PlaylistTemplate).options(selectinload(PlaylistTemplate.slots))
    if station_id:
        stmt = stmt.where(
            (PlaylistTemplate.station_id == station_id) | (PlaylistTemplate.station_id.is_(None))
        )
    stmt = stmt.order_by(PlaylistTemplate.name)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=PlaylistTemplateInDB, status_code=201)
async def create_template(
    data: PlaylistTemplateCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    template = PlaylistTemplate(
        name=data.name,
        description=data.description,
        station_id=data.station_id,
        is_active=data.is_active,
    )
    db.add(template)
    await db.flush()

    if data.slots:
        for i, slot_data in enumerate(data.slots):
            slot = TemplateSlot(
                template_id=template.id,
                position=slot_data.position if slot_data.position else i,
                asset_type=slot_data.asset_type,
                category=slot_data.category,
            )
            db.add(slot)

    await db.commit()
    await db.refresh(template)
    return template


@router.get("/asset-types")
async def list_asset_types(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Return distinct asset_type + category combinations from the assets table."""
    stmt = (
        select(Asset.asset_type, Asset.category)
        .distinct()
        .order_by(Asset.asset_type, Asset.category)
    )
    result = await db.execute(stmt)
    return [
        {"asset_type": row[0], "category": row[1]}
        for row in result.all()
    ]


@router.get("/{template_id}", response_model=PlaylistTemplateInDB)
async def get_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = (
        select(PlaylistTemplate)
        .options(selectinload(PlaylistTemplate.slots))
        .where(PlaylistTemplate.id == template_id)
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.patch("/{template_id}", response_model=PlaylistTemplateInDB)
async def update_template(
    template_id: UUID,
    data: PlaylistTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = (
        select(PlaylistTemplate)
        .options(selectinload(PlaylistTemplate.slots))
        .where(PlaylistTemplate.id == template_id)
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)

    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(PlaylistTemplate).where(PlaylistTemplate.id == template_id)
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(template)
    await db.commit()


# ==================== Slots ====================

@router.post("/slots", response_model=TemplateSlotInDB, status_code=201)
async def create_slot(
    data: TemplateSlotCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    slot = TemplateSlot(
        template_id=data.template_id,
        position=data.position,
        asset_type=data.asset_type,
        category=data.category,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return slot


@router.patch("/slots/{slot_id}", response_model=TemplateSlotInDB)
async def update_slot(
    slot_id: UUID,
    data: TemplateSlotUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(TemplateSlot).where(TemplateSlot.id == slot_id)
    result = await db.execute(stmt)
    slot = result.scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(slot, key, value)

    await db.commit()
    await db.refresh(slot)
    return slot


@router.delete("/slots/{slot_id}", status_code=204)
async def delete_slot(
    slot_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(TemplateSlot).where(TemplateSlot.id == slot_id)
    result = await db.execute(stmt)
    slot = result.scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    await db.delete(slot)
    await db.commit()
