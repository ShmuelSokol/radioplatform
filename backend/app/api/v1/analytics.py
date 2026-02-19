"""
Analytics and reporting endpoints.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, case, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.sponsor import Sponsor

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/play-counts")
async def play_counts(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Get play counts by asset, grouped by day."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            func.date(PlayLog.start_utc).label("date"),
            func.count(PlayLog.id).label("plays"),
        )
        .where(PlayLog.start_utc >= cutoff)
    )
    if station_id:
        stmt = stmt.where(PlayLog.station_id == station_id)

    stmt = stmt.group_by(func.date(PlayLog.start_utc)).order_by(func.date(PlayLog.start_utc))
    result = await db.execute(stmt)
    rows = result.all()

    return {
        "period_days": days,
        "data": [{"date": str(row.date), "plays": row.plays} for row in rows],
    }


@router.get("/top-assets")
async def top_assets(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Get most played assets."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            Asset.id,
            Asset.title,
            Asset.artist,
            Asset.asset_type,
            Asset.category,
            func.count(PlayLog.id).label("play_count"),
        )
        .join(Asset, PlayLog.asset_id == Asset.id)
        .where(PlayLog.start_utc >= cutoff)
    )
    if station_id:
        stmt = stmt.where(PlayLog.station_id == station_id)

    stmt = (
        stmt.group_by(Asset.id, Asset.title, Asset.artist, Asset.asset_type, Asset.category)
        .order_by(func.count(PlayLog.id).desc())
        .limit(limit)
    )
    result = await db.execute(stmt)

    return {
        "period_days": days,
        "assets": [
            {
                "id": str(row.id),
                "title": row.title,
                "artist": row.artist,
                "asset_type": row.asset_type,
                "category": row.category,
                "play_count": row.play_count,
            }
            for row in result.all()
        ],
    }


@router.get("/category-breakdown")
async def category_breakdown(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Get play counts grouped by asset category."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            func.coalesce(Asset.category, "uncategorized").label("category"),
            Asset.asset_type,
            func.count(PlayLog.id).label("play_count"),
        )
        .join(Asset, PlayLog.asset_id == Asset.id)
        .where(PlayLog.start_utc >= cutoff)
    )
    if station_id:
        stmt = stmt.where(PlayLog.station_id == station_id)

    stmt = stmt.group_by(Asset.category, Asset.asset_type).order_by(func.count(PlayLog.id).desc())
    result = await db.execute(stmt)

    return {
        "period_days": days,
        "categories": [
            {
                "category": row.category,
                "asset_type": row.asset_type,
                "play_count": row.play_count,
            }
            for row in result.all()
        ],
    }


@router.get("/hourly-distribution")
async def hourly_distribution(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Get play distribution by hour of day."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            extract("hour", PlayLog.start_utc).label("hour"),
            func.count(PlayLog.id).label("plays"),
        )
        .where(PlayLog.start_utc >= cutoff)
    )
    if station_id:
        stmt = stmt.where(PlayLog.station_id == station_id)

    stmt = stmt.group_by(extract("hour", PlayLog.start_utc)).order_by("hour")
    result = await db.execute(stmt)

    return {
        "period_days": days,
        "hours": [{"hour": int(row.hour), "plays": row.plays} for row in result.all()],
    }


@router.get("/summary")
async def analytics_summary(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Get high-level analytics summary."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    base_filter = [PlayLog.start_utc >= cutoff]
    if station_id:
        base_filter.append(PlayLog.station_id == station_id)

    # Total plays
    stmt = select(func.count(PlayLog.id)).where(*base_filter)
    total_plays = (await db.execute(stmt)).scalar() or 0

    # Unique assets played
    stmt = select(func.count(func.distinct(PlayLog.asset_id))).where(*base_filter)
    unique_assets = (await db.execute(stmt)).scalar() or 0

    # Total airtime (seconds)
    stmt = (
        select(func.sum(Asset.duration))
        .join(Asset, PlayLog.asset_id == Asset.id)
        .where(*base_filter)
    )
    total_seconds = float((await db.execute(stmt)).scalar() or 0)

    # Plays by source
    stmt = (
        select(PlayLog.source, func.count(PlayLog.id))
        .where(*base_filter)
        .group_by(PlayLog.source)
    )
    source_result = await db.execute(stmt)
    plays_by_source = {str(row[0]): row[1] for row in source_result.all()}

    return {
        "period_days": days,
        "total_plays": total_plays,
        "unique_assets": unique_assets,
        "total_airtime_hours": round(total_seconds / 3600, 1),
        "avg_plays_per_day": round(total_plays / max(days, 1), 1),
        "plays_by_source": plays_by_source,
    }
