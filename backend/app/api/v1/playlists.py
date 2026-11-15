"""
Playlist template CRUD endpoints — manage rotation patterns of asset types/categories.
"""
from uuid import UUID

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, require_manager
from app.models.asset import Asset
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.station import Station
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


# ==================== Generation ahead of time ====================


class GenerationSettings(BaseModel):
    station_id: UUID
    enabled: bool
    hours: int = Field(6, ge=1, le=48)
    artist_distance: int = Field(2, ge=0, le=20)
    min_vote: int = Field(0, ge=0, le=100)


async def _station(db: AsyncSession, station_id: UUID) -> Station:
    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


async def _queued_seconds(db: AsyncSession, station_id: UUID) -> float:
    result = await db.execute(
        select(func.coalesce(func.sum(func.coalesce(Asset.duration, 180)), 0))
        .select_from(QueueEntry)
        .join(Asset, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
    )
    return float(result.scalar() or 0)


@router.get("/generation")
async def get_generation(
    station_id: UUID,
    hours: int | None = None,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    """Settings, plus a dry run of what generation would queue next. Changes nothing."""
    from zoneinfo import ZoneInfo

    from app.services.playlist_generator_service import CONFIG_KEY, generate, settings_for

    station = await _station(db, station_id)
    settings = settings_for(station.automation_config)
    look = max(1, min(48, hours or settings.hours))
    queued = await _queued_seconds(db, station_id)
    # When generation is off the queue is days long; preview from now instead.
    start = datetime.now(UTC) + timedelta(seconds=queued if settings.enabled else 0)
    saved = (station.automation_config or {}).get(CONFIG_KEY) or {}
    plan = await generate(
        db,
        station,
        start,
        look,
        artist_distance=settings.artist_distance,
        min_vote=settings.min_vote,
        cursor=saved.get("cursor") or {},
    )
    try:
        tz = ZoneInfo(station.timezone or "UTC")
    except Exception:
        tz = UTC
    return {
        "station_id": str(station.id),
        "enabled": settings.enabled,
        "hours": settings.hours,
        "artist_distance": settings.artist_distance,
        "min_vote": settings.min_vote,
        "queued_hours": round(queued / 3600, 1),
        "preview_from": start,
        "preview_hours": look,
        "notes": plan.notes,
        "preview": [
            {
                "at": p.at,
                "local_time": p.at.astimezone(tz).strftime("%a %H:%M"),
                "block": p.block_name,
                "asset_type": p.asset_type,
                "category": p.category,
                "asset_id": str(p.asset_id),
                "title": p.title,
                "artist": p.artist,
                "duration": p.duration,
                "artist_rule_relaxed": p.artist_rule_relaxed,
            }
            for p in plan.picks
        ],
    }


@router.put("/generation")
async def set_generation(
    data: GenerationSettings,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    from app.services.playlist_generator_service import CONFIG_KEY

    station = await _station(db, data.station_id)
    config = dict(station.automation_config or {})
    saved = dict(config.get(CONFIG_KEY) or {})
    saved.update(
        enabled=data.enabled,
        hours=data.hours,
        artist_distance=data.artist_distance,
        min_vote=data.min_vote,
    )
    config[CONFIG_KEY] = saved
    station.automation_config = config
    await db.commit()
    return {
        "enabled": data.enabled,
        "hours": data.hours,
        "artist_distance": data.artist_distance,
        "min_vote": data.min_vote,
    }


@router.post("/generation/regenerate")
async def regenerate(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    """Throw away the automatically queued songs that have not started and generate again.

    Keeps what is playing, anything a person queued, requests, sponsor spots and
    timed items (time, weather).
    """
    from app.services.playlist_generator_service import settings_for
    from app.services.queue_replenish_service import QueueReplenishService

    station = await _station(db, station_id)
    if not settings_for(station.automation_config).enabled:
        raise HTTPException(status_code=409, detail="Playlist generation is switched off")
    removed = await db.execute(
        delete(QueueEntry).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == "auto",
            QueueEntry.preempt_at.is_(None),
        )
    )
    await db.commit()
    await QueueReplenishService(db, station_id).replenish()
    await db.commit()
    return {"removed": removed.rowcount, "queued_hours": round(await _queued_seconds(db, station_id) / 3600, 1)}


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
