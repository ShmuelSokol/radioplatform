"""
Channel stream management endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.channel_stream import ChannelStream
from app.schemas.channel import ChannelCreate, ChannelInDB, ChannelUpdate

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("", response_model=list[ChannelInDB])
async def list_channels(
    station_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChannelStream)
    if station_id:
        stmt = stmt.where(ChannelStream.station_id == station_id)
    stmt = stmt.order_by(ChannelStream.channel_name)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=ChannelInDB, status_code=201)
async def create_channel(
    data: ChannelCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = ChannelStream(**data.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.put("/{channel_id}", response_model=ChannelInDB)
async def update_channel(
    channel_id: UUID,
    data: ChannelUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(ChannelStream).where(ChannelStream.id == channel_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Channel not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(ChannelStream).where(ChannelStream.id == channel_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Channel not found")

    await db.delete(record)
    await db.commit()
