"""
Program blocks: one named event ("Weather Sponsors") that airs a run of items
(station ID, weather, a PSA, a sponsor spot, a programme) at set clock times.
See wiki/program-blocks.md.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, may_operate_station, require_manager
from app.core.exceptions import NotFoundError
from app.models.program_block import ProgramBlock
from app.models.station import Station
from app.models.user import User
from app.schemas.program_block import (
    BlockPreview,
    PreviewItem,
    PreviewOccurrence,
    ProgramBlockCreate,
    ProgramBlockOut,
    ProgramBlockUpdate,
)
from app.services.program_block_service import plan_blocks, reconcile_block_entries
from app.services.spot_schedule_service import reconcile_spot_slots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/program-blocks", tags=["program-blocks"])


async def _station(db: AsyncSession, station_id) -> Station:
    station = (
        await db.execute(select(Station).where(Station.id == station_id))
    ).scalar_one_or_none()
    if not station:
        raise NotFoundError("Station not found")
    return station


async def _get(db: AsyncSession, block_id: UUID) -> ProgramBlock:
    block = (
        await db.execute(select(ProgramBlock).where(ProgramBlock.id == block_id))
    ).scalar_one_or_none()
    if not block:
        raise NotFoundError("Block not found")
    return block


async def _allowed(db: AsyncSession, user: User, station_id) -> None:
    """A block puts things on a station's air: the same rule as operating it."""
    if not may_operate_station(user, await _station(db, station_id)):
        raise HTTPException(status_code=403, detail="You are not assigned to this station")


async def _requeue(db: AsyncSession, station_id) -> None:
    """Make the queue follow an edit right away."""
    try:
        # Sponsor places in blocks change where the sponsorships go.
        await reconcile_spot_slots(db, station_id)
        await reconcile_block_entries(db, station_id, rebuild=True)
        await db.commit()
    except Exception:
        # The periodic queue refill will pick the change up.
        await db.rollback()
        logger.exception("Program block refresh failed for station %s", station_id)


def _items(data) -> list[dict]:
    return [i.model_dump(mode="json", exclude_none=True) for i in data]


@router.get("", response_model=list[ProgramBlockOut])
async def list_program_blocks(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    result = await db.execute(
        select(ProgramBlock)
        .where(ProgramBlock.station_id == station_id)
        .order_by(ProgramBlock.name)
    )
    return list(result.scalars().all())


@router.get("/capabilities")
async def program_block_capabilities(_: User = Depends(require_manager)):
    """What blocks can do on this server, for scripts that set blocks up (the Daf Yomi job
    checks keep_hourly before it makes any block on a station that keeps its hourly items)."""
    return {"keep_hourly": True}


@router.get("/preview", response_model=BlockPreview)
async def preview_program_blocks(
    station_id: UUID,
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    """What the blocks will air over the coming hours, with each run's length."""
    station = await _station(db, station_id)
    found = await plan_blocks(db, station, hours=hours, dry_run=True)
    return BlockPreview(
        timezone=station.timezone or "UTC",
        hours=hours,
        occurrences=[
            PreviewOccurrence(
                block_id=o.block.id,
                block_name=o.block.name,
                at=o.at,
                local_time=o.local.strftime("%a %d %b %I:%M %p").replace(" 0", " "),
                timing_mode=o.block.timing_mode,
                latest_minutes=o.block.latest_minutes,
                seconds=o.seconds,
                items=[
                    PreviewItem(
                        kind=i.kind,
                        label=i.label,
                        title=i.title,
                        duration=i.duration,
                        problem=i.problem,
                        open_place=i.open_place,
                    )
                    for i in o.items
                ],
                problems=o.problems,
            )
            for o in found
        ],
    )


@router.post("", response_model=ProgramBlockOut, status_code=201)
async def create_program_block(
    data: ProgramBlockCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    await _allowed(db, user, data.station_id)
    block = ProgramBlock(
        station_id=data.station_id,
        name=data.name.strip(),
        is_active=data.is_active,
        days_of_week=data.days_of_week,
        times=data.times,
        timing_mode=data.timing_mode,
        latest_minutes=data.latest_minutes if data.timing_mode == "soft" else None,
        items=_items(data.items),
    )
    db.add(block)
    await db.commit()
    await db.refresh(block)
    out = ProgramBlockOut.model_validate(block)
    await _requeue(db, data.station_id)
    return out


@router.put("/{block_id}", response_model=ProgramBlockOut)
async def update_program_block(
    block_id: UUID,
    data: ProgramBlockUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    block = await _get(db, block_id)
    await _allowed(db, user, block.station_id)
    changes = data.model_dump(exclude_unset=True, exclude={"items", "latest_minutes"})
    for key, value in changes.items():
        if value is not None:
            setattr(block, key, value.strip() if key == "name" else value)
    if "latest_minutes" in data.model_fields_set:
        block.latest_minutes = data.latest_minutes  # None clears the limit
    if block.timing_mode != "soft":
        block.latest_minutes = None
    if data.items is not None:
        block.items = _items(data.items)
    station_id = block.station_id
    await db.commit()
    await db.refresh(block)
    out = ProgramBlockOut.model_validate(block)
    await _requeue(db, station_id)
    return out


@router.delete("/{block_id}", status_code=204)
async def delete_program_block(
    block_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    block = await _get(db, block_id)
    await _allowed(db, user, block.station_id)
    station_id = block.station_id
    await db.delete(block)
    await db.commit()
    await _requeue(db, station_id)
