"""
Sponsor spot schedules — when each sponsor's spot airs.
Managers set days, dayparts (morning/afternoon/evening/overnight) and plays
per daypart; the queue's clock slots follow.
"""

import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import station_guard as sg
from app.core.dependencies import get_db, require_manager
from app.core.exceptions import NotFoundError
from app.models.asset import Asset
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.schemas.spot_schedule import (
    OffAirWindow,
    PlannedSpot,
    SpotPlanPreview,
    SpotScheduleCreate,
    SpotScheduleOut,
    SpotScheduleUpdate,
    UnplacedSpot,
)
from app.services.spot_schedule_service import (
    DEFAULT_TIMING,
    plan_for_station,
    reconcile_spot_slots,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/spot-schedules",
    tags=["spot-schedules"],
    dependencies=[Depends(sg.station_write_guard(path={"schedule_id": sg.of_spot_schedule}))],
)


async def _refresh_queues(db: AsyncSession, station_ids: set) -> None:
    """Apply schedule changes to the affected stations' queues right away."""
    if None in station_ids:
        result = await db.execute(select(Station.id).where(Station.is_active.is_(True)))
        targets = [row[0] for row in result.all()]
    else:
        targets = list(station_ids)
    for station_id in targets:
        try:
            await reconcile_spot_slots(db, station_id)
            # Sponsor places inside program blocks are written by the block.
            from app.services.program_block_service import reconcile_block_entries

            await reconcile_block_entries(db, station_id, rebuild=True)
        except Exception:
            # The periodic queue refill will pick the change up.
            logger.exception("Spot slot refresh failed for station %s", station_id)


async def _get_or_404(db: AsyncSession, model, obj_id, label: str):
    record = (await db.execute(select(model).where(model.id == obj_id))).scalar_one_or_none()
    if not record:
        raise NotFoundError(f"{label} not found")
    return record


def _check_audio_owner(asset: Asset, sponsor_id) -> None:
    """A sponsor's schedule may only air that sponsor's audio (or unowned audio)."""
    if asset.sponsor_id is not None and str(asset.sponsor_id) != str(sponsor_id):
        raise HTTPException(status_code=422, detail="That audio belongs to a different sponsor")


@router.get("", response_model=list[SpotScheduleOut])
async def list_spot_schedules(
    sponsor_id: UUID | None = None,
    station_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(SpotSchedule).order_by(SpotSchedule.created_at)
    if sponsor_id:
        stmt = stmt.where(SpotSchedule.sponsor_id == sponsor_id)
    if station_id:
        stmt = stmt.where(
            (SpotSchedule.station_id == station_id) | (SpotSchedule.station_id.is_(None))
        )
    return (await db.execute(stmt)).scalars().all()


@router.get("/preview", response_model=SpotPlanPreview)
async def preview_spot_plan(
    station_id: UUID,
    days: int = Query(7, ge=1, le=31),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Look ahead: every sponsor spot planned for the next N days on a station,
    plus any spot that could not be given a slot."""
    station = await _get_or_404(db, Station, station_id, "Station")
    now = datetime.now(UTC)
    placements, unplaced = await plan_for_station(db, station, days=days, now=now)
    placements = [p for p in placements if p.at > now]  # not what already passed today
    # Dayparts wholly inside Shabbos/Yom Tov are skipped on purpose, not a problem to fix.
    skipped: dict = {}
    for u in unplaced:
        if u.blackout is not None:
            skipped[u.blackout] = skipped.get(u.blackout, 0) + 1
    unplaced = [u for u in unplaced if u.blackout is None]

    sponsor_ids = {p.schedule.sponsor_id for p in placements} | {
        u.schedule.sponsor_id for u in unplaced
    }
    asset_ids = {p.schedule.asset_id for p in placements}
    sponsors: dict = {}
    assets: dict = {}
    if sponsor_ids:
        rows = await db.execute(select(Sponsor.id, Sponsor.name).where(Sponsor.id.in_(sponsor_ids)))
        sponsors = {str(r[0]): r[1] for r in rows.all()}
    if asset_ids:
        rows = await db.execute(select(Asset.id, Asset.title).where(Asset.id.in_(asset_ids)))
        assets = {str(r[0]): r[1] for r in rows.all()}

    return SpotPlanPreview(
        station_id=station.id,
        timezone=station.timezone,
        days=days,
        planned=[
            PlannedSpot(
                scheduled_at=p.at,
                local_time=p.local.strftime("%Y-%m-%d %H:%M"),
                daypart=p.daypart,
                timing_mode=p.timing_mode,
                fixed=p.fixed,
                block=p.block,
                schedule_id=p.schedule.id,
                sponsor_id=p.schedule.sponsor_id,
                sponsor_name=sponsors.get(str(p.schedule.sponsor_id), "Unknown"),
                asset_id=p.schedule.asset_id,
                asset_title=assets.get(str(p.schedule.asset_id), "Unknown"),
            )
            for p in placements
        ],
        unplaced=[
            UnplacedSpot(
                local_date=u.local_date.isoformat(),
                daypart=u.daypart,
                schedule_id=u.schedule.id,
                sponsor_name=sponsors.get(str(u.schedule.sponsor_id), "Unknown"),
                reason=u.reason,
            )
            for u in unplaced
        ],
        off_air=[
            OffAirWindow(name=b.name, start=b.start, end=b.end, skipped_plays=n)
            for b, n in sorted(skipped.items(), key=lambda kv: kv[0].start)
        ],
    )


class TimingDefaults(BaseModel):
    station_id: UUID
    on_the_hour: Literal["hard", "soft"]
    other: Literal["hard", "soft"]


@router.get("/timing-defaults")
async def get_timing_defaults(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    station = await _get_or_404(db, Station, station_id, "Station")
    saved = (station.automation_config or {}).get("spot_timing") or {}
    return {**DEFAULT_TIMING, **{k: v for k, v in saved.items() if k in DEFAULT_TIMING}}


@router.put("/timing-defaults")
async def set_timing_defaults(
    data: TimingDefaults,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """How a sponsor spot honours its time when its own schedule does not say."""
    station = await _get_or_404(db, Station, data.station_id, "Station")
    config = dict(station.automation_config or {})
    config["spot_timing"] = {"on_the_hour": data.on_the_hour, "other": data.other}
    station.automation_config = config
    await db.flush()
    await _refresh_queues(db, {station.id})
    await db.commit()
    return config["spot_timing"]


@router.post("", response_model=SpotScheduleOut, status_code=201)
async def create_spot_schedule(
    data: SpotScheduleCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    sponsor = await _get_or_404(db, Sponsor, data.sponsor_id, "Sponsor")
    asset = await _get_or_404(db, Asset, data.asset_id, "Spot audio")
    if data.station_id:
        await _get_or_404(db, Station, data.station_id, "Station")

    _check_audio_owner(asset, sponsor.id)
    # Tie the audio to the sponsor so their portal shows its plays.
    if asset.sponsor_id is None:
        asset.sponsor_id = sponsor.id

    record = SpotSchedule(**data.model_dump())
    db.add(record)
    await db.flush()
    await _refresh_queues(db, {record.station_id})
    await db.commit()
    await db.refresh(record)
    return record


@router.put("/{schedule_id}", response_model=SpotScheduleOut)
async def update_spot_schedule(
    schedule_id: UUID,
    data: SpotScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = await _get_or_404(db, SpotSchedule, schedule_id, "Spot schedule")
    changes = data.model_dump(exclude_unset=True)
    affected = {record.station_id}

    if changes.get("asset_id"):
        asset = await _get_or_404(db, Asset, changes["asset_id"], "Spot audio")
        _check_audio_owner(asset, record.sponsor_id)
        if asset.sponsor_id is None:
            asset.sponsor_id = record.sponsor_id
    if changes.get("station_id"):
        await _get_or_404(db, Station, changes["station_id"], "Station")

    start = changes.get("start_date", record.start_date)
    end = changes.get("end_date", record.end_date)
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")

    for key, value in changes.items():
        setattr(record, key, value)
    affected.add(record.station_id)

    await db.flush()
    await _refresh_queues(db, affected)
    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{schedule_id}", status_code=204)
async def delete_spot_schedule(
    schedule_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = await _get_or_404(db, SpotSchedule, schedule_id, "Spot schedule")
    affected = {record.station_id}
    # Turn it off first so the queue drops its slots even if it was the last
    # schedule (an empty schedule list would fall back to the legacy single spot
    # only on the next refill, leaving this sponsor's slots queued meanwhile).
    record.is_active = False
    await db.flush()
    await _refresh_queues(db, affected)
    await db.delete(record)
    await db.commit()
