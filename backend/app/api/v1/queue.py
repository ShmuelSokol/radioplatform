import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_manager
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.user import User
from app.schemas.queue import QueueAdd, QueueBulkAdd, QueueEntryOut, QueueListResponse, QueueReorder

router = APIRouter(prefix="/stations/{station_id}/queue", tags=["queue"])


async def _check_advance(db: AsyncSession, station_id: uuid.UUID) -> QueueEntry | None:
    """Core playback engine: check if current track is done and auto-advance."""
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )
    current = result.scalar_one_or_none()
    if not current:
        return None

    # If no started_at, set it now
    if not current.started_at:
        current.started_at = datetime.now(timezone.utc)
        await db.commit()
        return current

    # Check if track duration has elapsed
    asset = current.asset
    duration = asset.duration if asset else 0
    if not duration:
        duration = 180  # default 3 min

    elapsed = (datetime.now(timezone.utc) - current.started_at).total_seconds()
    if elapsed < duration:
        return current  # still playing

    # Track finished — log it and advance
    log = PlayLog(
        id=uuid.uuid4(),
        station_id=station_id,
        asset_id=current.asset_id,
        start_utc=current.started_at,
        end_utc=datetime.now(timezone.utc),
        source="scheduler",
    )
    db.add(log)
    current.status = "played"

    # Find next pending
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
        .limit(1)
    )
    next_entry = result.scalar_one_or_none()
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)
        await db.commit()
        return next_entry

    await db.commit()
    return None


@router.get("")
async def get_queue(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Run the playback engine on every poll
    now_playing_entry = await _check_advance(db, station_id)

    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )
    entries = result.scalars().all()

    # Calculate elapsed/remaining for now playing
    np_data = None
    if now_playing_entry and now_playing_entry.started_at:
        asset = now_playing_entry.asset
        duration = asset.duration if asset else 180
        elapsed = (datetime.now(timezone.utc) - now_playing_entry.started_at).total_seconds()
        remaining = max(0, duration - elapsed)
        np_data = {
            "id": str(now_playing_entry.id),
            "station_id": str(now_playing_entry.station_id),
            "asset_id": str(now_playing_entry.asset_id),
            "position": now_playing_entry.position,
            "status": now_playing_entry.status,
            "asset": now_playing_entry.asset,
            "started_at": now_playing_entry.started_at.isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "remaining_seconds": round(remaining, 1),
        }

    entries_data = []
    for e in entries:
        d = {
            "id": str(e.id),
            "station_id": str(e.station_id),
            "asset_id": str(e.asset_id),
            "position": e.position,
            "status": e.status,
            "asset": {
                "id": str(e.asset.id),
                "title": e.asset.title,
                "artist": e.asset.artist,
                "album": e.asset.album,
                "duration": e.asset.duration,
                "file_path": e.asset.file_path,
                "album_art_path": e.asset.album_art_path,
                "metadata_extra": e.asset.metadata_extra,
                "created_by": str(e.asset.created_by) if e.asset.created_by else None,
                "asset_type": e.asset.asset_type,
                "category": e.asset.category,
            } if e.asset else None,
        }
        entries_data.append(d)

    return {
        "entries": entries_data,
        "total": len(entries_data),
        "now_playing": np_data,
    }


@router.get("/log")
async def get_play_log(
    station_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get recent play history."""
    result = await db.execute(
        select(PlayLog)
        .where(PlayLog.station_id == station_id)
        .order_by(PlayLog.start_utc.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return {
        "logs": [
            {
                "id": str(log.id),
                "asset_id": str(log.asset_id) if log.asset_id else None,
                "title": log.asset.title if log.asset else "Unknown",
                "artist": log.asset.artist if log.asset else None,
                "asset_type": log.asset.asset_type if log.asset else None,
                "start_utc": log.start_utc.isoformat(),
                "end_utc": log.end_utc.isoformat() if log.end_utc else None,
                "source": log.source.value if hasattr(log.source, 'value') else str(log.source),
            }
            for log in logs
        ],
        "total": len(logs),
    }


@router.post("/add", status_code=201)
async def add_to_queue(
    station_id: uuid.UUID,
    body: QueueAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )
    max_pos = result.scalar() or 0
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=body.asset_id,
        position=max_pos + 1, status="pending",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"id": str(entry.id), "position": entry.position, "status": entry.status}


@router.post("/bulk-add", status_code=201)
async def bulk_add_to_queue(
    station_id: uuid.UUID,
    body: QueueBulkAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(func.max(QueueEntry.position))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
    )
    max_pos = result.scalar() or 0
    count = 0
    for asset_id in body.asset_ids:
        max_pos += 1
        entry = QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset_id,
            position=max_pos, status="pending",
        )
        db.add(entry)
        count += 1
    await db.commit()
    return {"message": f"Added {count} items to queue", "count": count}


@router.post("/play-next", status_code=201)
async def play_next(
    station_id: uuid.UUID,
    body: QueueAdd,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .values(position=QueueEntry.position + 1)
    )
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )
    current = result.scalar_one_or_none()
    next_pos = (current.position + 1) if current else 1
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=body.asset_id,
        position=next_pos, status="pending",
    )
    db.add(entry)
    await db.commit()
    return {"id": str(entry.id), "position": entry.position, "message": "Queued as next"}


@router.post("/skip")
async def skip_current(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )
    current = result.scalar_one_or_none()
    if current:
        # Log the skip
        if current.started_at:
            log = PlayLog(
                id=uuid.uuid4(), station_id=station_id, asset_id=current.asset_id,
                start_utc=current.started_at, end_utc=datetime.now(timezone.utc),
                source="manual",
            )
            db.add(log)
        current.status = "skipped"

    # Advance to next
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).limit(1)
    )
    next_entry = result.scalar_one_or_none()
    if next_entry:
        next_entry.status = "playing"
        next_entry.started_at = datetime.now(timezone.utc)
        await db.commit()
        return {"message": "Skipped", "now_playing": str(next_entry.asset_id)}

    await db.commit()
    return {"message": "Queue empty", "now_playing": None}


@router.post("/move-up")
async def move_up(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Move a queue entry up (lower position number)."""
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry or entry.status != "pending":
        raise NotFoundError("Entry not found or not pending")

    # Find the entry above it
    result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position < entry.position,
        )
        .order_by(QueueEntry.position.desc()).limit(1)
    )
    above = result.scalar_one_or_none()
    if above:
        above.position, entry.position = entry.position, above.position
        await db.commit()
    return {"message": "Moved up"}


@router.post("/move-down")
async def move_down(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Move a queue entry down (higher position number)."""
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry or entry.status != "pending":
        raise NotFoundError("Entry not found or not pending")

    result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position > entry.position,
        )
        .order_by(QueueEntry.position.asc()).limit(1)
    )
    below = result.scalar_one_or_none()
    if below:
        below.position, entry.position = entry.position, below.position
        await db.commit()
    return {"message": "Moved down"}


@router.put("/reorder")
async def reorder_queue(
    station_id: uuid.UUID,
    body: QueueReorder,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == body.entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    entry.position = body.new_position
    await db.commit()
    return {"message": "Reordered"}


@router.delete("/{entry_id}", status_code=204)
async def remove_from_queue(
    station_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(select(QueueEntry).where(QueueEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise NotFoundError("Queue entry not found")
    await db.delete(entry)
    await db.commit()


@router.post("/start")
async def start_playback(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )
    current = result.scalar_one_or_none()
    if current:
        if not current.started_at:
            current.started_at = datetime.now(timezone.utc)
            await db.commit()
        return {"message": "Already playing", "now_playing": str(current.asset_id)}

    result = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).limit(1)
    )
    next_entry = result.scalar_one_or_none()
    if not next_entry:
        return {"message": "Queue empty", "now_playing": None}

    next_entry.status = "playing"
    next_entry.started_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Started", "now_playing": str(next_entry.asset_id)}
