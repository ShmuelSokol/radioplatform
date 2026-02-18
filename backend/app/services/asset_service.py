import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.asset import Asset
from app.services.storage_service import generate_asset_key, upload_file


async def create_asset(
    db: AsyncSession,
    title: str,
    filename: str,
    file_data: bytes,
    content_type: str,
    user_id: uuid.UUID | None = None,
) -> Asset:
    s3_key = generate_asset_key(filename)
    await upload_file(file_data, s3_key, content_type)

    asset = Asset(
        title=title,
        file_path=s3_key,
        created_by=user_id,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


async def get_asset(db: AsyncSession, asset_id: uuid.UUID) -> Asset:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise NotFoundError(f"Asset {asset_id} not found")
    return asset


async def list_assets(
    db: AsyncSession, skip: int = 0, limit: int = 50
) -> tuple[list[Asset], int]:
    count_result = await db.execute(select(func.count(Asset.id)))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Asset).offset(skip).limit(limit).order_by(Asset.created_at.desc())
    )
    assets = list(result.scalars().all())
    return assets, total


async def update_asset_metadata(
    db: AsyncSession,
    asset_id: uuid.UUID,
    metadata: dict,
) -> Asset:
    asset = await get_asset(db, asset_id)
    if "duration" in metadata:
        asset.duration = metadata["duration"]
    if "artist" in metadata:
        asset.artist = metadata["artist"]
    if "album" in metadata:
        asset.album = metadata["album"]
    if "title" in metadata and metadata["title"]:
        asset.title = metadata["title"]
    asset.metadata_extra = metadata
    await db.flush()
    await db.refresh(asset)
    return asset


async def set_album_art(db: AsyncSession, asset_id: uuid.UUID, art_path: str) -> Asset:
    asset = await get_asset(db, asset_id)
    asset.album_art_path = art_path
    await db.flush()
    await db.refresh(asset)
    return asset


async def delete_asset(db: AsyncSession, asset_id: uuid.UUID) -> None:
    asset = await get_asset(db, asset_id)
    await db.delete(asset)
    await db.flush()
