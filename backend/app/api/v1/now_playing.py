"""
Now-playing endpoints — query current playback state, resolve active blocks.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_dj_or_manager, require_station_operator
from app.models.user import User
from app.schemas.schedule import NowPlaying, NowPlayingUpdate
from app.services.scheduling import SchedulingService

router = APIRouter(prefix="/now-playing", tags=["now-playing"])

# A "playing" queue item this long past its own end is left over from a player that
# stopped, not something on air
_STALE_GRACE_SECONDS = 120


@router.get("")
async def stations_on_air(db: AsyncSession = Depends(get_db)):
    """Public: which stations have something on air right now, in one call for the
    station list's "On Air" / "Offline" pills: a queue item playing (and not long past
    its end) or a live show that is live. {"playing": [station_id, ...]}"""
    from datetime import UTC, timedelta

    from sqlalchemy import select

    from app.models.asset import Asset
    from app.models.live_show import LiveShow, LiveShowStatus
    from app.models.queue_entry import QueueEntry

    now = datetime.now(UTC)
    on_air: set[str] = set()
    rows = (await db.execute(
        select(QueueEntry.station_id, QueueEntry.started_at, Asset.duration)
        .join(Asset, Asset.id == QueueEntry.asset_id, isouter=True)
        .where(QueueEntry.status == "playing")
    )).all()
    for station_id, started_at, duration in rows:
        if started_at is not None and duration:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
            if start + timedelta(seconds=float(duration) + _STALE_GRACE_SECONDS) < now:
                continue
        on_air.add(str(station_id))
    live = (await db.execute(
        select(LiveShow.station_id).where(LiveShow.status == LiveShowStatus.LIVE)
    )).scalars().all()
    on_air.update(str(s) for s in live)
    return {"playing": sorted(on_air)}


@router.get("/{station_id}", response_model=Optional[NowPlaying])
async def get_now_playing(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get current now-playing state for a station.

    The listener page polls this when its WebSocket keeps failing, and uses the
    result as if it were a WebSocket message. A station that plays from its queue
    keeps no now-playing record, so answer with the same message the WebSocket sends.
    """
    from fastapi.responses import JSONResponse

    from app.api.v1.websocket import _cached_queue_now_playing

    queue_payload = await _cached_queue_now_playing(str(station_id), db)
    if queue_payload is not None:
        return JSONResponse(queue_payload)
    service = SchedulingService(db)
    record = await service.get_now_playing(station_id)
    return record


@router.get("/{station_id}/active-block")
async def get_active_block(
    station_id: UUID,
    at_time: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get the currently active schedule block for a station.
    Useful for debugging or manual override.
    """
    service = SchedulingService(db)
    block = await service.get_active_block_for_station(station_id, at_time)
    if not block:
        return {"message": "No active block found", "station_id": str(station_id)}
    return {
        "block_id": str(block.id),
        "name": block.name,
        "start_time": str(block.start_time),
        "end_time": str(block.end_time),
        "recurrence_type": block.recurrence_type,
    }


# Changing what a station shows on air is operating it: a DJ limited to other
# stations (or any non-assigned login on the protected FM feed) is refused.
@router.patch("/{station_id}", response_model=NowPlaying, dependencies=[Depends(require_station_operator)])
async def update_now_playing(
    station_id: UUID,
    data: NowPlayingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_dj_or_manager),
):
    """
    Manually update now-playing state (e.g., for testing or manual control).
    """
    service = SchedulingService(db)
    duration = None
    if data.ends_at and data.started_at:
        duration = (data.ends_at - data.started_at).total_seconds()

    record = await service.update_now_playing(
        station_id=station_id,
        asset_id=data.asset_id,
        block_id=data.block_id,
        duration_seconds=duration,
    )
    return record


@router.delete("/{station_id}", status_code=204, dependencies=[Depends(require_station_operator)])
async def clear_now_playing(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_dj_or_manager),
):
    """Clear now-playing state for a station."""
    service = SchedulingService(db)
    await service.clear_now_playing(station_id)
