"""
Analytics and reporting endpoints.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, may_operate_station, require_manager
from app.core.exceptions import ForbiddenError
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.station import Station
from app.models.user import User, UserRole

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _stations_in_scope(db: AsyncSession, user: User, station_id: UUID | None) -> list | None:
    """The stations this login may read figures for (None = every station).

    A manager limited to some stations (User.station_ids) sees only those, the
    same rule as operating them (see wiki/station-permissions.md)."""
    if station_id:
        station = await db.get(Station, station_id)
        if station is not None and not may_operate_station(user, station):
            raise ForbiddenError("You are not allowed on this station")
        return [station_id]
    if user.role == UserRole.ADMIN:
        return None
    stations = (await db.execute(select(Station))).scalars().all()
    allowed = [st.id for st in stations if may_operate_station(user, st)]
    return None if len(allowed) == len(stations) else allowed


def _in_scope(stmt, ids: list | None):
    return stmt if ids is None else stmt.where(PlayLog.station_id.in_(ids))


async def _local_hours(db: AsyncSession, cutoff: datetime, ids: list | None) -> list[tuple[datetime, int]]:
    """Plays per station-local hour: (local start of the hour, plays).

    Grouped in the database by station, UTC day and UTC hour (a few thousand rows
    at most), then moved into each station's own time zone here, so an evening in
    New York is not counted as the next morning."""
    day = func.date(PlayLog.start_utc)
    hour = extract("hour", PlayLog.start_utc)
    stmt = _in_scope(
        select(PlayLog.station_id, day.label("day"), hour.label("hour"), func.count(PlayLog.id).label("plays"))
        .where(PlayLog.start_utc >= cutoff),
        ids,
    ).group_by(PlayLog.station_id, day, hour)
    rows = (await db.execute(stmt)).all()
    zones = {str(sid): tz for sid, tz in (await db.execute(select(Station.id, Station.timezone))).all()}
    out = []
    for row in rows:
        d = row.day if isinstance(row.day, date) else date.fromisoformat(str(row.day)[:10])
        try:
            tz = ZoneInfo(zones.get(str(row.station_id)) or "UTC")
        except Exception:
            tz = timezone.utc
        out.append((datetime.combine(d, time(int(row.hour)), tzinfo=timezone.utc).astimezone(tz), row.plays))
    return out


@router.get("/play-counts")
async def play_counts(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Get play counts by asset, grouped by day."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ids = await _stations_in_scope(db, user, station_id)
    per_day: Counter = Counter()
    for local, plays in await _local_hours(db, cutoff, ids):
        per_day[local.date()] += plays

    return {
        "period_days": days,
        "data": [{"date": str(d), "plays": per_day[d]} for d in sorted(per_day)],
    }


@router.get("/top-assets")
async def top_assets(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
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
    stmt = _in_scope(stmt, await _stations_in_scope(db, user, station_id))

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
    user: User = Depends(require_manager),
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
    stmt = _in_scope(stmt, await _stations_in_scope(db, user, station_id))

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
    user: User = Depends(require_manager),
):
    """Get play distribution by hour of day."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ids = await _stations_in_scope(db, user, station_id)
    per_hour: Counter = Counter()
    for local, plays in await _local_hours(db, cutoff, ids):
        per_hour[local.hour] += plays

    return {
        "period_days": days,
        "hours": [{"hour": h, "plays": per_hour[h]} for h in sorted(per_hour)],
    }


@router.get("/summary")
async def analytics_summary(
    station_id: UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Get high-level analytics summary."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    base_filter = [PlayLog.start_utc >= cutoff]
    ids = await _stations_in_scope(db, user, station_id)
    if ids is not None:
        base_filter.append(PlayLog.station_id.in_(ids))

    # Total plays
    stmt = select(func.count(PlayLog.id)).where(*base_filter)
    total_plays = (await db.execute(stmt)).scalar() or 0

    # Unique assets played
    stmt = select(func.count(func.distinct(PlayLog.asset_id))).where(*base_filter)
    unique_assets = (await db.execute(stmt)).scalar() or 0

    # Total airtime (seconds): how long each play really aired (start to end),
    # never more than the item; a play still on air (no end) counts the item
    if db.get_bind().dialect.name == "sqlite":
        aired = (func.julianday(PlayLog.end_utc) - func.julianday(PlayLog.start_utc)) * 86400
        shorter = func.min
    else:
        aired = extract("epoch", PlayLog.end_utc - PlayLog.start_utc)
        shorter = func.least
    item = func.coalesce(Asset.duration, 0)
    airtime = case(
        (PlayLog.end_utc.is_(None), item),
        (Asset.duration.is_(None), aired),
        else_=shorter(aired, item),
    )
    stmt = (
        select(func.sum(airtime))
        .select_from(PlayLog)
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
