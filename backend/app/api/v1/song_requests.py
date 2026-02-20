"""Song request API -- public submit + admin manage."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.core.exceptions import NotFoundError
from app.models.song_request import SongRequest, RequestStatus
from app.models.user import User
from app.schemas.song_request import (
    SongRequestCreate,
    SongRequestUpdate,
    SongRequestInDB,
    SongRequestListResponse,
)

router = APIRouter(prefix="/song-requests", tags=["song-requests"])


@router.post("", response_model=SongRequestInDB, status_code=201)
async def submit_request(body: SongRequestCreate, db: AsyncSession = Depends(get_db)):
    """Public: submit a song request (no auth)."""
    req = SongRequest(
        station_id=body.station_id,
        requester_name=body.requester_name,
        song_title=body.song_title,
        song_artist=body.song_artist,
        requester_message=body.requester_message,
        status=RequestStatus.PENDING,
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)
    return req


@router.get("", response_model=SongRequestListResponse)
async def list_requests(
    station_id: str | None = Query(None),
    status: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Admin: list song requests with optional filters."""
    q = select(SongRequest)
    count_q = select(func.count(SongRequest.id))
    if station_id:
        q = q.where(SongRequest.station_id == station_id)
        count_q = count_q.where(SongRequest.station_id == station_id)
    if status:
        q = q.where(SongRequest.status == status)
        count_q = count_q.where(SongRequest.status == status)
    q = q.order_by(SongRequest.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(q)
    requests = result.scalars().all()
    total = (await db.execute(count_q)).scalar() or 0
    return SongRequestListResponse(requests=requests, total=total)


@router.patch("/{request_id}", response_model=SongRequestInDB)
async def update_request(
    request_id: uuid.UUID,
    body: SongRequestUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Admin: approve/reject/update a song request."""
    result = await db.execute(
        select(SongRequest).where(SongRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise NotFoundError("Song request not found")

    if body.status:
        req.status = body.status
        req.reviewed_by = user.id
        req.reviewed_at = datetime.now(timezone.utc)
    if body.asset_id is not None:
        req.asset_id = body.asset_id

    await db.flush()
    await db.refresh(req)
    return req


@router.delete("/{request_id}", status_code=204)
async def delete_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Admin: delete a song request."""
    result = await db.execute(
        select(SongRequest).where(SongRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise NotFoundError("Song request not found")
    await db.delete(req)


# Public: get pending requests count for a station (shown on listen page)
@router.get("/station/{station_id}/count")
async def get_request_count(station_id: str, db: AsyncSession = Depends(get_db)):
    """Public: count pending song requests for a station."""
    result = await db.execute(
        select(func.count(SongRequest.id)).where(
            SongRequest.station_id == station_id,
            SongRequest.status == RequestStatus.PENDING,
        )
    )
    return {"count": result.scalar() or 0}
