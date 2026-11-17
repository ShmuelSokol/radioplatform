"""
Databank: the library as tables, the way KBR Studio's "KBR Database" window
shows it (Songs.db, Spots.db, the jingle list, Blocks.db) plus the play log of
any day with start and end times to the millisecond. See wiki/databank.md.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, may_operate_station, require_manager, require_staff
from app.core.exceptions import NotFoundError
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User
from app.services.asset_service import GENERATED_TITLE_PREFIXES

router = APIRouter(prefix="/databank", tags=["databank"])


def file_name(path: str | None) -> str:
    """The last part of a storage path or URL, without a query string."""
    if not path:
        return ""
    return path.split("?")[0].rstrip("/").split("/")[-1]


def _tz(station: Station | None) -> ZoneInfo:
    try:
        return ZoneInfo(station.timezone) if station and station.timezone else ZoneInfo("UTC")
    except Exception:
        return ZoneInfo("UTC")


async def _station(db: AsyncSession, station_id) -> Station:
    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    if not station:
        raise NotFoundError("Station not found")
    return station


# KBR Studio's "Mpeg" filter: MPEG audio files
MPEG_EXTS = ("mp3", "mp2", "mpg", "mpeg")

# Column name in the table -> what it sorts on
SORTS = {
    "artist": Asset.artist,
    "title": Asset.title,
    "type": Asset.category,
    "vote": Asset.vote,
    "length": Asset.duration,
    "intro": Asset.intro_seconds,
    "file": Asset.file_path,
    "added": Asset.created_at,
}


@router.get("/items")
async def list_items(
    asset_type: str = Query("music", description="music, jingle, spot, shiur, ..."),
    station_id: uuid.UUID | None = Query(None, description="Last played on this station (all stations if empty)"),
    search: str | None = Query(None, description="Find: artist, title, type or file"),
    category: str | None = Query(None, description="Only this type (category)"),
    files: str = Query("all", description="all; mpeg = MPEG audio files (.mp3/.mp2/.mpg); present = playable (not marked do_not_play)"),
    sort: str = Query("artist"),
    desc: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    """One page of the library, sorted by any column, with when each item last played."""
    lp = select(PlayLog.asset_id.label("asset_id"), func.max(PlayLog.start_utc).label("last_played"))
    if station_id:
        lp = lp.where(PlayLog.station_id == station_id)
    lp = lp.group_by(PlayLog.asset_id).subquery()

    conditions = [Asset.asset_type == asset_type]
    for prefix in GENERATED_TITLE_PREFIXES:
        conditions.append(~Asset.title.like(f"{prefix}%"))
    if category:
        conditions.append(func.lower(Asset.category) == category.lower())
    if files == "mpeg":
        low = func.lower(Asset.file_path)
        conditions.append(or_(*[low.like(f"%.{ext}") | low.like(f"%.{ext}?%") for ext in MPEG_EXTS]))
    elif files == "present":
        conditions.append(Asset.file_path != "")
        conditions.append(or_(Asset.category.is_(None), func.lower(Asset.category) != "do_not_play"))
    if search:
        # % and _ are matched as typed (file names like track_01), not as LIKE wildcards
        typed = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pat = f"%{typed}%"
        conditions.append(or_(
            Asset.title.ilike(pat, escape="\\"), Asset.artist.ilike(pat, escape="\\"),
            Asset.category.ilike(pat, escape="\\"), Asset.file_path.ilike(pat, escape="\\"),
        ))

    total = (await db.execute(select(func.count(Asset.id)).where(*conditions))).scalar() or 0

    col = lp.c.last_played if sort == "last_played" else SORTS.get(sort, Asset.artist)
    order = [col.desc().nulls_last() if desc else col.asc().nulls_last(), Asset.title.asc(), Asset.id.asc()]
    rows = (await db.execute(
        select(Asset, lp.c.last_played)
        .outerjoin(lp, lp.c.asset_id == Asset.id)
        .where(*conditions)
        .order_by(*order)
        .offset(skip).limit(limit)
    )).all()

    return {
        "total": total,
        "items": [
            {
                "id": str(a.id),
                "artist": a.artist,
                "title": a.title,
                "type": a.category,
                "asset_type": a.asset_type,
                "vote": a.vote,
                "length": a.duration,
                "intro": a.intro_seconds,
                "file": file_name(a.file_path),
                "last_played": last.isoformat() if last else None,
            }
            for a, last in rows
        ],
    }


@router.get("/spots")
async def list_spots(
    station_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    """Spots.db: every sponsorship recording with its category, last end date and length.

    The end date is the latest end date of the spot schedules that air it
    (on this station, or on any when no station is given). A spot with no
    schedule has none.
    """
    conditions = [Asset.asset_type == "spot"]
    for prefix in GENERATED_TITLE_PREFIXES:
        conditions.append(~Asset.title.like(f"{prefix}%"))
    assets = (await db.execute(select(Asset).where(*conditions).order_by(Asset.title))).scalars().all()

    sq = select(SpotSchedule)
    if station_id:
        sq = sq.where(or_(SpotSchedule.station_id == station_id, SpotSchedule.station_id.is_(None)))
    schedules = (await db.execute(sq)).scalars().all()
    by_asset: dict[str, list[SpotSchedule]] = {}
    for s in schedules:
        by_asset.setdefault(str(s.asset_id), []).append(s)

    station = await _station(db, station_id) if station_id else None
    today = datetime.now(_tz(station)).date()

    out = []
    for a in assets:
        mine = by_asset.get(str(a.id), [])
        ends = [s.end_date for s in mine if s.end_date]
        open_ended = any(s.end_date is None for s in mine)
        end = None if open_ended or not ends else max(ends)
        out.append({
            "id": str(a.id),
            "title": a.title,
            "file": file_name(a.file_path),
            "category": a.category,
            "end_date": end.isoformat() if end else None,
            "open_ended": open_ended,
            "schedules": len(mine),
            "active": any(s.is_active for s in mine),
            "expired": bool(mine) and not open_ended and end is not None and end < today,
            "length": a.duration,
        })
    return {"today": today.isoformat(), "spots": out}


class EndDateIn(BaseModel):
    end_date: date | None


@router.patch("/spots/{asset_id}/end-date")
async def set_spot_end_date(
    asset_id: uuid.UUID,
    body: EndDateIn,
    station_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Move the end date of every schedule that airs this spot (KBR Studio edits it in the row)."""
    sq = select(SpotSchedule).where(SpotSchedule.asset_id == asset_id)
    if station_id:
        sq = sq.where(or_(SpotSchedule.station_id == station_id, SpotSchedule.station_id.is_(None)))
    schedules = (await db.execute(sq)).scalars().all()
    if not schedules:
        raise HTTPException(status_code=409, detail="This spot has no schedule yet. Make one in Spot Schedule first.")
    every_station_checked = False
    for s in schedules:
        if s.station_id is None:
            # Airs on every station, the protected FM station included
            if not every_station_checked:
                from app.api.v1.spot_schedules import _check_every_station

                await _check_every_station(db, user)
                every_station_checked = True
        elif not may_operate_station(user, await _station(db, s.station_id)):
            raise HTTPException(status_code=403, detail="You are not assigned to this station")
        if body.end_date and s.start_date and body.end_date < s.start_date:
            raise HTTPException(status_code=422, detail="The end date is before the schedule starts")
    for s in schedules:
        s.end_date = body.end_date
    await db.flush()

    from app.api.v1.spot_schedules import _refresh_queues

    await _refresh_queues(db, {s.station_id for s in schedules})
    await db.commit()
    return {"updated": len(schedules), "end_date": body.end_date.isoformat() if body.end_date else None}


@router.get("/log")
async def day_log(
    station_id: uuid.UUID,
    day: date | None = Query(None, description="Station-local day, YYYY-MM-DD (today if empty)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    """Everything that aired on one station-local day, oldest first, to the millisecond."""
    station = await _station(db, station_id)
    tz = _tz(station)
    if day is None:
        day = datetime.now(tz).date()
    start = datetime.combine(day, time(0), tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0), tz).astimezone(timezone.utc)
    logs = (await db.execute(
        select(PlayLog)
        .options(selectinload(PlayLog.asset))
        .where(PlayLog.station_id == station_id, PlayLog.start_utc >= start, PlayLog.start_utc < end)
        .order_by(PlayLog.start_utc.asc())
        .limit(5000)
    )).scalars().all()
    return {
        "day": day.isoformat(),
        "timezone": str(tz),
        "logs": [
            {
                "id": str(log.id),
                "asset_id": str(log.asset_id) if log.asset_id else None,
                "title": log.asset.title if log.asset else "Unknown",
                "artist": log.asset.artist if log.asset else None,
                "asset_type": log.asset.asset_type if log.asset else None,
                "file": file_name(log.asset.file_path) if log.asset else "",
                "start_utc": log.start_utc.isoformat(),
                "end_utc": log.end_utc.isoformat() if log.end_utc else None,
                "source": log.source.value if hasattr(log.source, "value") else str(log.source),
            }
            for log in logs
        ],
    }
