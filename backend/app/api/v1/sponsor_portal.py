"""
Sponsor Portal endpoints — the sponsor's spots, play history, upcoming plays, stats.
Accessible only by authenticated sponsors (viewing their own data).

A sponsor's spots are the library assets tied to them: assets with
sponsor_id set, the asset at the sponsor's audio_file_path, and any asset used
by one of their spot schedules. Plays are matched by asset, so every airing
counts no matter how it got on air (clock slot, manual, queue fill).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_sponsor
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User
from app.schemas.sponsor_portal import (
    PlayHistoryEntry,
    PlayHistoryResponse,
    SponsorSpot,
    SponsorStats,
    UpcomingScheduleEntry,
)
from app.services.spot_schedule_service import as_utc, station_tz

router = APIRouter(prefix="/sponsor-portal", tags=["sponsor-portal"])


async def _get_sponsor_for_user(db, user: User) -> Sponsor | None:
    """Look up the Sponsor record linked to this user."""
    result = await db.execute(select(Sponsor).where(Sponsor.user_id == user.id).limit(1))
    return result.scalars().first()


async def _sponsor_asset_ids(db: AsyncSession, sponsor: Sponsor) -> list:
    """IDs of every library asset that belongs to this sponsor."""
    conditions = [
        Asset.sponsor_id == sponsor.id,
        Asset.id.in_(select(SpotSchedule.asset_id).where(SpotSchedule.sponsor_id == sponsor.id)),
    ]
    if sponsor.audio_file_path:
        conditions.append(Asset.file_path == sponsor.audio_file_path)
    result = await db.execute(select(Asset.id).where(or_(*conditions)))
    return [row[0] for row in result.all()]


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/spots", response_model=list[SponsorSpot])
async def get_my_spots(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    if not sponsor:
        return []
    asset_ids = await _sponsor_asset_ids(db, sponsor)
    if not asset_ids:
        return []

    now = datetime.now(UTC)
    assets = (
        (await db.execute(select(Asset).where(Asset.id.in_(asset_ids)).order_by(Asset.title)))
        .scalars()
        .all()
    )

    last_rows = await db.execute(
        select(PlayLog.asset_id, func.max(PlayLog.start_utc), func.count(PlayLog.id))
        .where(PlayLog.asset_id.in_(asset_ids))
        .group_by(PlayLog.asset_id)
    )
    last = {str(r[0]): (r[1], r[2]) for r in last_rows.all()}

    month_rows = await db.execute(
        select(PlayLog.asset_id, func.count(PlayLog.id))
        .where(PlayLog.asset_id.in_(asset_ids), PlayLog.start_utc >= _month_start(now))
        .group_by(PlayLog.asset_id)
    )
    month = {str(r[0]): r[1] for r in month_rows.all()}

    next_rows = await db.execute(
        select(QueueEntry.asset_id, func.min(QueueEntry.preempt_at))
        .where(
            QueueEntry.asset_id.in_(asset_ids),
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at > now,
        )
        .group_by(QueueEntry.asset_id)
    )
    upcoming = {str(r[0]): r[1] for r in next_rows.all()}

    sched_rows = await db.execute(
        select(SpotSchedule.asset_id).where(
            SpotSchedule.sponsor_id == sponsor.id, SpotSchedule.is_active.is_(True)
        )
    )
    scheduled = {str(r[0]) for r in sched_rows.all()}

    spots = []
    for asset in assets:
        key = str(asset.id)
        last_at, total = last.get(key, (None, 0))
        next_at = upcoming.get(key)
        spots.append(
            SponsorSpot(
                asset_id=asset.id,
                title=asset.title,
                duration_seconds=asset.duration,
                last_played_at=as_utc(last_at) if last_at else None,
                next_play_at=as_utc(next_at) if next_at else None,
                plays_month=month.get(key, 0),
                plays_alltime=total,
                scheduled=key in scheduled,
            )
        )
    return spots


@router.get("/play-history", response_model=PlayHistoryResponse)
async def get_play_history(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    asset_ids = await _sponsor_asset_ids(db, sponsor) if sponsor else []
    if not asset_ids:
        return PlayHistoryResponse(entries=[], total=0, page=page, limit=limit)

    total = (
        await db.execute(select(func.count(PlayLog.id)).where(PlayLog.asset_id.in_(asset_ids)))
    ).scalar() or 0

    result = await db.execute(
        select(PlayLog, Station.name.label("station_name"), Asset.title.label("asset_title"))
        .join(Station, PlayLog.station_id == Station.id)
        .outerjoin(Asset, PlayLog.asset_id == Asset.id)
        .where(PlayLog.asset_id.in_(asset_ids))
        .order_by(PlayLog.start_utc.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )

    entries = []
    for play_log, station_name, asset_title in result.all():
        duration = None
        if play_log.start_utc and play_log.end_utc:
            duration = (play_log.end_utc - play_log.start_utc).total_seconds()
        entries.append(
            PlayHistoryEntry(
                id=play_log.id,
                station_name=station_name or "Unknown",
                asset_title=asset_title or "Sponsor spot",
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
    """The sponsor's spots that are actually in the station queues, soonest first."""
    sponsor = await _get_sponsor_for_user(db, user)
    asset_ids = await _sponsor_asset_ids(db, sponsor) if sponsor else []
    if not asset_ids:
        return []

    now = datetime.now(UTC)
    result = await db.execute(
        select(QueueEntry.preempt_at, Station.name, Station.timezone, Asset.title)
        .join(Station, QueueEntry.station_id == Station.id)
        .join(Asset, QueueEntry.asset_id == Asset.id)
        .where(
            QueueEntry.asset_id.in_(asset_ids),
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at > now,
        )
        .order_by(QueueEntry.preempt_at)
        .limit(90)
    )

    upcoming = []
    for preempt_at, station_name, tz_name, asset_title in result.all():
        at = as_utc(preempt_at)
        local = at.astimezone(station_tz(tz_name))
        upcoming.append(
            UpcomingScheduleEntry(
                estimated_date=local.strftime("%Y-%m-%d"),
                station_name=station_name,
                time_slot=local.strftime("%I:%M %p").lstrip("0"),
                asset_title=asset_title,
                scheduled_at=at,
            )
        )
    return upcoming


@router.get("/stats", response_model=SponsorStats)
async def get_sponsor_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_sponsor),
):
    sponsor = await _get_sponsor_for_user(db, user)
    asset_ids = await _sponsor_asset_ids(db, sponsor) if sponsor else []
    if not asset_ids:
        return SponsorStats(total_plays_month=0, total_plays_alltime=0)

    now = datetime.now(UTC)
    played = PlayLog.asset_id.in_(asset_ids)

    total_alltime = (await db.execute(select(func.count(PlayLog.id)).where(played))).scalar() or 0
    total_month = (
        await db.execute(
            select(func.count(PlayLog.id)).where(played, PlayLog.start_utc >= _month_start(now))
        )
    ).scalar() or 0
    last_played = (await db.execute(select(func.max(PlayLog.start_utc)).where(played))).scalar()
    next_row = (
        await db.execute(
            select(QueueEntry.preempt_at, Station.timezone)
            .join(Station, QueueEntry.station_id == Station.id)
            .where(
                QueueEntry.asset_id.in_(asset_ids),
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at > now,
            )
            .order_by(QueueEntry.preempt_at)
            .limit(1)
        )
    ).first()
    next_at = as_utc(next_row[0]) if next_row else None
    next_label = None
    if next_row:
        local = next_at.astimezone(station_tz(next_row[1]))
        next_label = (
            f"{local.strftime('%a %b')} {local.day}, {local.strftime('%I:%M %p').lstrip('0')}"
        )

    return SponsorStats(
        total_plays_month=total_month,
        total_plays_alltime=total_alltime,
        next_scheduled=next_label,
        next_scheduled_at=next_at,
        last_played=as_utc(last_played) if last_played else None,
    )
