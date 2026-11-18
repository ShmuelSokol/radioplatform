import hashlib
import asyncio
import logging
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
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
    converted_data, duration, out_ext = await asyncio.to_thread(convert_audio, file_data, source_name, target_format)

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
            bpm, tempo_category = await asyncio.to_thread(detect_bpm, converted_data, source_name)
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

    # Auto-detect release date from MusicBrainz for music assets
    if asset_type == "music" and artist:
        try:
            from app.services.musicbrainz_service import lookup_release_date
            rd = await lookup_release_date(title, artist)
            if rd:
                asset.release_date = rd
                await db.flush()
                logger.info("Auto-detected release_date for '%s': %s", title, rd)
        except Exception:
            logger.warning("Release date auto-detection failed for '%s'", title, exc_info=True)

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
    result = await db.execute(
        select(Asset, Sponsor.name.label("sponsor_name"))
        .outerjoin(Sponsor, Asset.sponsor_id == Sponsor.id)
        .where(Asset.id == asset_id)
    )
    row = result.one_or_none()
    if not row:
        raise NotFoundError(f"Asset {asset_id} not found")
    asset = row[0]
    asset.sponsor_name = row[1]
    return asset


# Titles the time and weather service gives its recordings (weather_spot_service)
GENERATED_TITLE_PREFIXES = ("Time Announcement - ", "Weather Report - ")


async def list_assets(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
    asset_type: str | None = None,
    search: str | None = None,
    category: str | None = None,
    title_search: str | None = None,
    artist_search: str | None = None,
    album_search: str | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    hide_generated: bool = False,
) -> tuple[list[dict], int]:
    # Build shared WHERE conditions
    conditions = []
    if hide_generated:
        # Time and weather recordings made for one hour or day: wrong at any other time
        for prefix in GENERATED_TITLE_PREFIXES:
            conditions.append(~Asset.title.like(f"{prefix}%"))
    if asset_type:
        conditions.append(Asset.asset_type == asset_type)
    if category:
        conditions.append(func.lower(Asset.category) == category.lower())
    if search:
        like_pat = f"%{search}%"
        conditions.append(or_(
            Asset.title.ilike(like_pat),
            Asset.artist.ilike(like_pat),
            Asset.album.ilike(like_pat),
        ))
    if title_search:
        conditions.append(Asset.title.ilike(f"%{title_search}%"))
    if artist_search:
        conditions.append(Asset.artist.ilike(f"%{artist_search}%"))
    if album_search:
        conditions.append(Asset.album.ilike(f"%{album_search}%"))
    if duration_min is not None:
        conditions.append(Asset.duration >= duration_min)
    if duration_max is not None:
        conditions.append(Asset.duration <= duration_max)

    count_q = select(func.count(Asset.id))
    for cond in conditions:
        count_q = count_q.where(cond)
    count_result = await db.execute(count_q)
    total = count_result.scalar() or 0

    # Subquery for last played time
    last_played_sq = (
        select(func.max(PlayLog.start_utc))
        .where(PlayLog.asset_id == Asset.id)
        .correlate(Asset)
        .scalar_subquery()
        .label("last_played_at")
    )

    q = (
        select(Asset, last_played_sq, Sponsor.name.label("sponsor_name"))
        .outerjoin(Sponsor, Asset.sponsor_id == Sponsor.id)
    )
    for cond in conditions:
        q = q.where(cond)
    result = await db.execute(
        q.offset(skip).limit(limit).order_by(Asset.created_at.desc())
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
    from app.models.queue_entry import QueueEntry

    asset = await get_asset(db, asset_id)
    # Deleting the item on air would cascade away its playing queue entry and leave the
    # station silent until dead-air recovery: refuse; press Next first.
    on_air = (await db.execute(
        select(QueueEntry.id).where(QueueEntry.asset_id == asset_id, QueueEntry.status == "playing").limit(1)
    )).scalar_one_or_none()
    if on_air is not None:
        raise ConflictError("This item is on air. Press Next (or wait for it to end), then delete it.")
    # Spot schedules would vanish silently (CASCADE) and program block items would
    # point at nothing: refuse and name them, so the manager moves them first.
    # Queued (not yet playing) entries and playlist lines are still removed with it.
    from app.models.program_block import ProgramBlock
    from app.models.spot_schedule import SpotSchedule

    uses = []
    spots = (await db.execute(
        select(SpotSchedule.name).where(SpotSchedule.asset_id == asset_id)
    )).scalars().all()
    if spots:
        uses.append("spot schedule " + ", ".join(f"'{n}'" if n else "(unnamed)" for n in spots))
    blocks = (await db.execute(select(ProgramBlock.name, ProgramBlock.items))).all()
    in_blocks = [
        name for name, items in blocks
        if any(isinstance(i, dict) and str(i.get("asset_id")) == str(asset_id) for i in (items or []))
    ]
    if in_blocks:
        uses.append("program block " + ", ".join(f"'{n}'" for n in in_blocks))
    if uses:
        raise ConflictError(
            "This item is used by " + "; ".join(uses) + ". Change or remove those first, then delete it."
        )
    await db.delete(asset)
    await db.flush()


async def bulk_update_category(
    db: AsyncSession, asset_ids: list[uuid.UUID], category: str
) -> int:
    result = await db.execute(
        update(Asset).where(Asset.id.in_(asset_ids)).values(category=category)
    )
    await db.flush()
    return result.rowcount


DO_NOT_PLAY = "do_not_play"
# Where the category a song had before "do not play" is kept, so switching it back
# on returns the song to its rotation.
CATEGORY_BEFORE_KEY = "category_before_do_not_play"


def is_do_not_play(asset: Asset) -> bool:
    return (asset.category or "").strip().lower() == DO_NOT_PLAY


async def set_do_not_play(db: AsyncSession, asset_id: uuid.UUID, on: bool) -> dict:
    """Take a song off the air for good (or put it back), in one step from the control room.

    On: the category becomes do_not_play (no automatic pick uses it, see
    models/asset.py) and the copies waiting in every station's queue are removed.
    Left alone: the copy on air, a copy the playout engine already holds as its next
    track (deleting that lost track of what Main was playing), and timed copies
    (their time slot would be left empty). Off: the category it had before comes back;
    nothing is queued again, the next automatic draw may pick it.
    """
    from sqlalchemy import delete, select

    from app.models.queue_entry import QueueEntry
    from app.services.playout_engine import held_entry_ids

    asset = await get_asset(db, asset_id)
    extra = dict(asset.metadata_extra or {})
    removed = kept = 0
    if on:
        if not is_do_not_play(asset):
            extra[CATEGORY_BEFORE_KEY] = asset.category
            asset.metadata_extra = extra
            asset.category = DO_NOT_PLAY
        rows = (await db.execute(
            select(QueueEntry.id, QueueEntry.station_id, QueueEntry.status, QueueEntry.preempt_at).where(
                QueueEntry.asset_id == asset.id, QueueEntry.status.in_(("pending", "playing"))
            )
        )).all()
        held: dict = {}
        drop = []
        for entry_id, station_id, status, preempt_at in rows:
            if station_id not in held:
                held[station_id] = held_entry_ids(station_id)
            if status == "playing" or preempt_at is not None or entry_id in held[station_id]:
                kept += 1
            else:
                drop.append(entry_id)
        if drop:
            # status again in SQL: an entry that went on air since the read stays
            result = await db.execute(
                delete(QueueEntry)
                .where(QueueEntry.id.in_(drop), QueueEntry.status == "pending")
                .execution_options(synchronize_session=False)
            )
            removed = result.rowcount or 0
            kept += len(drop) - removed
    elif is_do_not_play(asset):
        asset.category = extra.pop(CATEGORY_BEFORE_KEY, None)
        asset.metadata_extra = extra
    await db.flush()
    await db.refresh(asset)
    return {
        "id": str(asset.id),
        "category": asset.category,
        "do_not_play": is_do_not_play(asset),
        "removed_from_queue": removed,
        "still_to_play": kept,
    }
