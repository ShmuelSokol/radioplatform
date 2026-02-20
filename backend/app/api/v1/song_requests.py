"""Song request API -- public submit + admin manage."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.dependencies import get_db, require_manager
from app.core.exceptions import NotFoundError
from app.models.asset import Asset
from app.models.song_request import SongRequest, RequestStatus
from app.models.user import User
from app.schemas.song_request import (
    SongRequestCreate,
    SongRequestUpdate,
    SongRequestInDB,
    SongRequestListResponse,
    SongRequestSubmitResponse,
)
from app.services.song_request_service import (
    fuzzy_match_asset,
    check_auto_approve,
    add_to_queue,
    get_queue_position_info,
)

router = APIRouter(prefix="/song-requests", tags=["song-requests"])


@router.post("", response_model=SongRequestSubmitResponse, status_code=201)
async def submit_request(body: SongRequestCreate, db: AsyncSession = Depends(get_db)):
    """Public: submit a song request with fuzzy matching and auto-approval."""
    req = SongRequest(
        station_id=body.station_id,
        requester_name=body.requester_name,
        song_title=body.song_title,
        song_artist=body.song_artist,
        requester_message=body.requester_message,
        status=RequestStatus.PENDING,
    )

    # Fuzzy match against library
    matched_asset, confidence = await fuzzy_match_asset(
        db, body.song_title, body.song_artist, body.station_id
    )

    response_extra = {
        "matched_asset_title": None,
        "matched_asset_artist": None,
        "match_confidence": round(confidence, 3),
        "auto_approved": False,
        "queue_position": None,
        "songs_ahead": None,
        "estimated_wait_minutes": None,
    }

    if matched_asset:
        req.asset_id = matched_asset.id
        response_extra["matched_asset_title"] = matched_asset.title
        response_extra["matched_asset_artist"] = matched_asset.artist

        # Check auto-approve eligibility
        can_auto = await check_auto_approve(db, matched_asset, body.station_id)
        if can_auto:
            req.status = RequestStatus.QUEUED
            # reviewed_by stays NULL to distinguish auto from manual
            req.reviewed_at = datetime.now(timezone.utc)

            db.add(req)
            await db.flush()

            # Add to playback queue
            position = await add_to_queue(db, str(matched_asset.id), body.station_id)
            pos_info = await get_queue_position_info(db, body.station_id, position)

            response_extra["auto_approved"] = True
            response_extra["queue_position"] = position
            response_extra["songs_ahead"] = pos_info["songs_ahead"]
            response_extra["estimated_wait_minutes"] = pos_info["estimated_wait_minutes"]

            await db.refresh(req)
            return SongRequestSubmitResponse(
                id=req.id,
                station_id=req.station_id,
                requester_name=req.requester_name,
                song_title=req.song_title,
                song_artist=req.song_artist,
                requester_message=req.requester_message,
                asset_id=req.asset_id,
                status=req.status,
                created_at=req.created_at,
                **response_extra,
            )

    # Not auto-approved — save as pending
    db.add(req)
    await db.flush()
    await db.refresh(req)
    return SongRequestSubmitResponse(
        id=req.id,
        station_id=req.station_id,
        requester_name=req.requester_name,
        song_title=req.song_title,
        song_artist=req.song_artist,
        requester_message=req.requester_message,
        asset_id=req.asset_id,
        status=req.status,
        created_at=req.created_at,
        **response_extra,
    )


# Analytics endpoint — MUST be before /{request_id} to avoid path conflict
@router.get("/analytics/top-requested")
async def top_requested(
    station_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Admin: get most-requested songs grouped by matched asset."""
    q = (
        select(
            SongRequest.asset_id,
            func.count(SongRequest.id).label("request_count"),
            func.max(SongRequest.song_title).label("song_title"),
            func.max(SongRequest.song_artist).label("song_artist"),
        )
        .where(SongRequest.asset_id.isnot(None))
        .group_by(SongRequest.asset_id)
        .order_by(func.count(SongRequest.id).desc())
        .limit(limit)
    )
    if station_id:
        q = q.where(SongRequest.station_id == station_id)

    result = await db.execute(q)
    rows = result.all()

    # Fetch asset details for matched assets
    asset_ids = [row.asset_id for row in rows if row.asset_id]
    assets_map = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(Asset.id.in_(asset_ids))
        )
        for asset in asset_result.scalars().all():
            assets_map[str(asset.id)] = asset

    items = []
    for row in rows:
        asset = assets_map.get(str(row.asset_id))
        items.append({
            "asset_id": str(row.asset_id),
            "request_count": row.request_count,
            "requested_title": row.song_title,
            "requested_artist": row.song_artist,
            "library_title": asset.title if asset else None,
            "library_artist": asset.artist if asset else None,
        })

    return {"top_requested": items}


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
    q = select(SongRequest).options(joinedload(SongRequest.asset))
    count_q = select(func.count(SongRequest.id))
    if station_id:
        q = q.where(SongRequest.station_id == station_id)
        count_q = count_q.where(SongRequest.station_id == station_id)
    if status:
        q = q.where(SongRequest.status == status)
        count_q = count_q.where(SongRequest.status == status)
    q = q.order_by(SongRequest.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(q)
    requests = result.unique().scalars().all()
    total = (await db.execute(count_q)).scalar() or 0

    # Build response with matched asset info
    request_dicts = []
    for req in requests:
        d = SongRequestInDB.model_validate(req)
        if req.asset:
            d.matched_asset_title = req.asset.title
            d.matched_asset_artist = req.asset.artist
        request_dicts.append(d)

    return SongRequestListResponse(requests=request_dicts, total=total)


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
