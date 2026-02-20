"""
Sponsor Portal endpoints — play history, upcoming schedule, and stats.
Accessible only by authenticated sponsors (viewing their own data).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_sponsor
from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User
from app.schemas.sponsor_portal import (
    PlayHistoryEntry,
    PlayHistoryResponse,
    SponsorStats,
    UpcomingScheduleEntry,
)

router = APIRouter(prefix="/sponsor-portal", tags=["sponsor-portal"])


async def _get_sponsor_for_user(db, user: User) -> Sponsor | None:
    """Look up the Sponsor record linked to this user."""
    result = await db.execute(select(Sponsor).where(Sponsor.user_id == user.id))
    return result.scalar_one_or_none()


@router.get("/play-history", response_model=PlayHistoryResponse)
async def get_play_history(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    if not sponsor:
        return PlayHistoryResponse(entries=[], total=0, page=page, limit=limit)

    # Get all asset IDs that belong to this sponsor (via audio_file_path match)
    # PlayLog entries with source='ad' and matching sponsor's asset
    base_query = (
        select(PlayLog, Station.name.label("station_name"), Asset.title.label("asset_title"))
        .join(Station, PlayLog.station_id == Station.id)
        .outerjoin(Asset, PlayLog.asset_id == Asset.id)
        .where(PlayLog.source == PlaySource.AD)
    )

    # Count total
    count_query = (
        select(func.count(PlayLog.id))
        .where(PlayLog.source == PlaySource.AD)
    )

    # If sponsor has a specific audio_file_path, filter by that asset
    if sponsor.audio_file_path:
        asset_subq = select(Asset.id).where(Asset.file_path == sponsor.audio_file_path).scalar_subquery()
        base_query = base_query.where(PlayLog.asset_id == asset_subq)
        count_query = count_query.where(PlayLog.asset_id == asset_subq)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * limit
    data_query = base_query.order_by(PlayLog.start_utc.desc()).offset(offset).limit(limit)
    result = await db.execute(data_query)
    rows = result.all()

    entries = []
    for row in rows:
        play_log = row[0]
        station_name = row[1] or "Unknown"
        asset_title = row[2] or "Ad Spot"
        duration = None
        if play_log.start_utc and play_log.end_utc:
            duration = (play_log.end_utc - play_log.start_utc).total_seconds()
        entries.append(
            PlayHistoryEntry(
                id=play_log.id,
                station_name=station_name,
                asset_title=asset_title,
                start_utc=play_log.start_utc,
                end_utc=play_log.end_utc,
                duration_seconds=duration,
            )
        )

    return PlayHistoryResponse(entries=entries, total=total, page=page, limit=limit)


@router.get("/upcoming-schedule", response_model=list[UpcomingScheduleEntry])
async def get_upcoming_schedule(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    if not sponsor:
        return []

    # Build projected schedule based on sponsor's target_rules
    upcoming = []
    if sponsor.target_rules:
        rules = sponsor.target_rules
        hour_start = rules.get("hour_start", 6)
        hour_end = rules.get("hour_end", 22)

        # Get all active stations
        stations_result = await db.execute(select(Station).where(Station.is_active.is_(True)))
        stations = stations_result.scalars().all()

        now = datetime.now(timezone.utc)
        for day_offset in range(30):
            date = now + timedelta(days=day_offset)
            date_str = date.strftime("%Y-%m-%d")
            for station in stations[:3]:  # Limit to first 3 stations
                upcoming.append(
                    UpcomingScheduleEntry(
                        estimated_date=date_str,
                        station_name=station.name,
                        time_slot=f"{hour_start}:00 - {hour_end}:00",
                        asset_title=sponsor.name,
                    )
                )

    return upcoming[:90]  # Cap at 90 entries


@router.get("/stats", response_model=SponsorStats)
async def get_sponsor_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    if not sponsor:
        return SponsorStats(total_plays_month=0, total_plays_alltime=0)

    # Base filter: ad plays
    base_filter = [PlayLog.source == PlaySource.AD]
    if sponsor.audio_file_path:
        asset_subq = select(Asset.id).where(Asset.file_path == sponsor.audio_file_path).scalar_subquery()
        base_filter.append(PlayLog.asset_id == asset_subq)

    # Total all-time
    all_result = await db.execute(select(func.count(PlayLog.id)).where(*base_filter))
    total_alltime = all_result.scalar() or 0

    # Total this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_result = await db.execute(
        select(func.count(PlayLog.id)).where(*base_filter, PlayLog.start_utc >= month_start)
    )
    total_month = month_result.scalar() or 0

    return SponsorStats(
        total_plays_month=total_month,
        total_plays_alltime=total_alltime,
        next_scheduled=None,
    )
