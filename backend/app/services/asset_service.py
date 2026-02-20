import hashlib
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.sponsor import Sponsor
from app.services.audio_convert_service import CONVERT_FORMATS, convert_audio
from app.services.storage_service import generate_asset_key, upload_file

logger = logging.getLogger(__name__)


async def create_asset(
    db: AsyncSession,
    title: str,
    filename: str,
    file_data: bytes,
    content_type: str,
    user_id: uuid.UUID | None = None,
    original_filename: str | None = None,
    target_format: str = "mp3",
    artist: str | None = None,
    album: str | None = None,
    asset_type: str = "music",
    category: str | None = None,
) -> Asset:
    # Use original_filename for conversion detection; fall back to filename
    source_name = original_filename or filename

    # Diagnostic: log raw upload fingerprint
    raw_hash = hashlib.md5(file_data[:4096]).hexdigest()
    logger.info(
        "create_asset: title='%s', source='%s', raw_size=%d, raw_hash_4k=%s, target=%s",
        title, source_name, len(file_data), raw_hash, target_format,
    )

    # Convert to target format and extract duration
    converted_data, duration, out_ext = convert_audio(file_data, source_name, target_format)

    # Generate storage key with the correct extension
    store_filename = _force_extension(filename, out_ext)
    s3_key = generate_asset_key(store_filename)

    # Determine MIME type
    fmt_config = CONVERT_FORMATS.get(target_format)
    mime = fmt_config["mime"] if fmt_config else content_type

    # Diagnostic: log what we're about to upload
    upload_hash = hashlib.md5(converted_data[:4096]).hexdigest()
    logger.info(
        "create_asset UPLOAD: key='%s', size=%d, upload_hash_4k=%s, duration=%s",
        s3_key, len(converted_data), upload_hash, duration,
    )

    await upload_file(converted_data, s3_key, mime)

    # Auto-detect BPM and tempo category for music assets
    metadata_extra = None
    if asset_type == "music" and not category:
        try:
            from app.services.bpm_service import detect_bpm
            bpm, tempo_category = detect_bpm(converted_data, source_name)
            if bpm is not None:
                category = tempo_category
                metadata_extra = {"bpm": bpm}
                logger.info("Auto-categorized '%s': BPM=%.1f → %s", title, bpm, category)
        except Exception:
            logger.warning("BPM detection failed for '%s'", title, exc_info=True)

    asset = Asset(
        title=title,
        file_path=s3_key,
        duration=duration,
        created_by=user_id,
        artist=artist,
        album=album,
        asset_type=asset_type,
        category=category,
        metadata_extra=metadata_extra,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)

    if duration is not None:
        logger.info("Asset '%s' created with duration=%.2fs", title, duration)
    else:
        logger.info("Asset '%s' created (duration unknown)", title)

    return asset


def _force_extension(filename: str, ext: str) -> str:
    """Replace the file extension."""
    if not ext.startswith("."):
        ext = f".{ext}"
    if "." in filename:
        base = filename.rsplit(".", 1)[0]
        return f"{base}{ext}"
    return f"{filename}{ext}"


async def get_asset(db: AsyncSession, asset_id: uuid.UUID) -> Asset:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise NotFoundError(f"Asset {asset_id} not found")
    return asset


async def list_assets(
    db: AsyncSession, skip: int = 0, limit: int = 50
) -> tuple[list[dict], int]:
    count_result = await db.execute(select(func.count(Asset.id)))
    total = count_result.scalar() or 0

    # Subquery for last played time
    last_played_sq = (
        select(func.max(PlayLog.start_utc))
        .where(PlayLog.asset_id == Asset.id)
        .correlate(Asset)
        .scalar_subquery()
        .label("last_played_at")
    )

    result = await db.execute(
        select(Asset, last_played_sq, Sponsor.name.label("sponsor_name"))
        .outerjoin(Sponsor, Asset.sponsor_id == Sponsor.id)
        .offset(skip)
        .limit(limit)
        .order_by(Asset.created_at.desc())
    )
    rows = result.all()
    assets = []
    for row in rows:
        asset = row[0]
        asset.last_played_at = row[1]
        asset.sponsor_name = row[2]
        assets.append(asset)
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
