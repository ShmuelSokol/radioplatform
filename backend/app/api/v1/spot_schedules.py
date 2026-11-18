"""
Sponsor spot schedules — when each sponsor's spot airs.
Managers set days, dayparts (morning/afternoon/evening/overnight) and plays
per daypart; the queue's clock slots follow.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import station_guard as sg
from app.core.dependencies import get_db, may_operate_every_station, require_manager, require_staff
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
    plan_for_station,
    reconcile_spot_slots,
    room_warnings,
    timing_choices,
)
from app.services.weather_spot_service import half_hour_time_check

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/spot-schedules",
    tags=["spot-schedules"],
    dependencies=[Depends(sg.station_write_guard(path={"schedule_id": sg.of_spot_schedule}))],
)


# A save must answer within a second (S-6). One station's refresh takes 20-80 ms,
# so a few stations are refreshed inside the request; a schedule for every station
# (80+ stations: 2-6 s) is applied just after the save commits, in the background,
# a few stations at a time, each in its own session. Spots are queued at least
# 2 minutes ahead (SAFETY_MARGIN), so a refresh a few seconds later still lands
# before any changed slot airs, and the scheduler's queue refill is the backstop.
INLINE_MAX_STATIONS = 3
BACKGROUND_CONCURRENCY = 4
session_factory = None  # tests point this at the test database; None = app.db.engine
_background: set[asyncio.Task] = set()


async def _refresh_station(db: AsyncSession, station_id) -> None:
    from app.services.program_block_service import reconcile_block_entries
    from app.services.station_modes import hard_start_on, soften_hard_items

    await reconcile_spot_slots(db, station_id)
    # Sponsor places inside program blocks are written by the block.
    await reconcile_block_entries(db, station_id, rebuild=True)
    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    if station is not None and not hard_start_on(station):
        # The rebuilt block items come back hard: HS is off, so they wait for the song
        await soften_hard_items(db, station)


async def _refresh_queues(db: AsyncSession, station_ids: set) -> list:
    """Apply schedule changes to the affected stations' queues.

    Refreshes up to INLINE_MAX_STATIONS stations in this session (the caller
    commits) and returns the rest, which the caller hands to
    `refresh_in_background` once its commit is done."""
    if None in station_ids:
        result = await db.execute(select(Station.id).where(Station.is_active.is_(True)))
        targets = [row[0] for row in result.all()]
    else:
        targets = list(station_ids)
    if len(targets) > INLINE_MAX_STATIONS:
        return targets
    for station_id in targets:
        try:
            await _refresh_station(db, station_id)
        except Exception:
            # The periodic queue refill will pick the change up.
            logger.exception("Spot slot refresh failed for station %s", station_id)
    return []


def refresh_in_background(station_ids: list, then_delete_schedule=None) -> None:
    """Refresh these stations' queues after the save has committed. With
    `then_delete_schedule`, that (already switched off) schedule is deleted once
    every station has dropped its slots."""
    if not station_ids:
        return

    async def run():
        factory = session_factory
        if factory is None:
            from app.db.engine import async_session_factory as factory
        gate = asyncio.Semaphore(BACKGROUND_CONCURRENCY)

        async def one(station_id):
            async with gate:
                try:
                    async with factory() as session:
                        await _refresh_station(session, station_id)
                        await session.commit()
                except Exception:
                    logger.exception("Spot slot refresh failed for station %s", station_id)

        started = time.monotonic()
        await asyncio.gather(*(one(s) for s in station_ids))
        if then_delete_schedule is not None:
            try:
                async with factory() as session:
                    await session.execute(sa_delete(SpotSchedule).where(SpotSchedule.id == then_delete_schedule))
                    await session.commit()
            except Exception:
                logger.exception("Could not delete spot schedule %s", then_delete_schedule)
        logger.info("Spot schedule change applied to %d stations in %.1f s",
                    len(station_ids), time.monotonic() - started)

    task = asyncio.get_running_loop().create_task(run())
    _background.add(task)  # keep a reference so the task is not collected mid-run
    task.add_done_callback(_background.discard)


async def wait_for_background() -> None:
    """Tests: wait until every background refresh has finished."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


EVERY_STATION_REFUSED = (
    "A spot schedule for every station also airs on stations your login is not set up "
    "to operate. Choose a station, or ask an admin."
)


async def _check_every_station(db: AsyncSession, user) -> None:
    if not await may_operate_every_station(db, user):
        raise HTTPException(status_code=403, detail=EVERY_STATION_REFUSED)


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


@router.get("/station-day")
async def sponsorships_today(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_staff),
):
    """The control room's Sponsors list: today's sponsorship plays on one station."""
    from app.core.dependencies import may_operate_station
    from app.services.sponsor_item import station_day

    station = await _get_or_404(db, Station, station_id, "Station")
    if not may_operate_station(user, station):
        raise HTTPException(status_code=403, detail="You are not set up for this station")
    return await station_day(db, station)


@router.get("/for-asset/{asset_id}")
async def sponsorship_of_asset(
    asset_id: UUID,
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_staff),
):
    """The control room's Sponsor panel: the sponsor and contracts that use this
    recording, today's and coming plays on the station, and recent missed plays."""
    from app.services.sponsor_item import sponsor_item

    from app.core.dependencies import may_operate_station

    asset = await _get_or_404(db, Asset, asset_id, "Spot")
    station = await _get_or_404(db, Station, station_id, "Station")
    if not may_operate_station(user, station):
        raise HTTPException(status_code=403, detail="You are not set up for this station")
    # Other stations' contracts only for staff who may see every station
    every = await may_operate_every_station(db, user)
    return await sponsor_item(db, asset, station, show_elsewhere=every)


@router.get("/preview", response_model=SpotPlanPreview)
async def preview_spot_plan(
    station_id: UUID,
    days: int = Query(7, ge=1, le=31),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Look ahead: every sponsor spot planned for the next N days on a station,
    plus any spot that could not be given a slot."""
    from app.services.station_modes import spots_on

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
                moved_from=p.moved_from,
                schedule_id=p.schedule.id,
                sponsor_id=p.schedule.sponsor_id,
                sponsor_name=sponsors.get(str(p.schedule.sponsor_id), "Unknown"),
                asset_id=p.schedule.asset_id,
                asset_title=assets.get(str(p.schedule.asset_id), "Unknown"),
            )
            for p in placements
        ],
        unplaced=_grouped_unplaced(unplaced, sponsors),
        off_air=[
            OffAirWindow(name=b.name, start=b.start, end=b.end, skipped_plays=n)
            for b, n in sorted(skipped.items(), key=lambda kv: kv[0].start)
        ],
        spots_off=not spots_on(station),
    )


class TimingDefaults(BaseModel):
    station_id: UUID
    on_the_hour: Literal["hard", "soft"]
    other: Literal["hard", "soft"]
    # Left out (an older page): the station keeps what it has
    half_hour: Literal["hard", "soft"] | None = None
    half_hour_time_check: bool | None = None


def _timing_out(config: dict | None) -> dict:
    config = config or {}
    return {
        **timing_choices(config.get("spot_timing")),
        "half_hour_time_check": half_hour_time_check(config),
    }


@router.get("/timing-defaults")
async def get_timing_defaults(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    station = await _get_or_404(db, Station, station_id, "Station")
    return _timing_out(station.automation_config)


@router.put("/timing-defaults")
async def set_timing_defaults(
    data: TimingDefaults,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """How a sponsor spot (and the automatic time and weather) honours its time when
    its own schedule does not say: on the hour, on the half hour, and the rest."""
    station = await _get_or_404(db, Station, data.station_id, "Station")
    config = dict(station.automation_config or {})
    saved = dict(config.get("spot_timing") or {})
    saved.update({"on_the_hour": data.on_the_hour, "other": data.other})
    if data.half_hour is not None:
        saved["half_hour"] = data.half_hour
    config["spot_timing"] = saved
    if data.half_hour_time_check is not None:
        config["half_hour_time_check"] = data.half_hour_time_check
    station.automation_config = config
    await db.flush()
    await _retime_announcements(db, station)
    later = await _refresh_queues(db, {station.id})
    await db.commit()
    refresh_in_background(later)
    return _timing_out(config)


async def _retime_announcements(db: AsyncSession, station: Station) -> None:
    """Give the time checks and weather already queued the station's new choice, so
    switching the half hour to "on the second" counts from the next half hour, not
    from 48 hours on. With HS off they stay soft (HS on hands out the timing again)."""
    from app.models.queue_entry import QueueEntry
    from app.services.spot_schedule_service import default_timing
    from app.services.station_modes import hard_start_on
    from app.services.weather_spot_service import station_announcement_zone

    if not hard_start_on(station):
        return
    zone = await station_announcement_zone(db, station.id)
    choices = (station.automation_config or {}).get("spot_timing")
    rows = (
        await db.execute(
            select(QueueEntry)
            .join(Asset, Asset.id == QueueEntry.asset_id)
            .where(
                QueueEntry.station_id == station.id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                (QueueEntry.source.is_(None)) | (QueueEntry.source == "auto"),
                Asset.category.in_(("time_announcement", "weather_spot")),
            )
        )
    ).scalars().all()
    for entry in rows:
        at = entry.preempt_at if entry.preempt_at.tzinfo else entry.preempt_at.replace(tzinfo=UTC)
        mode = default_timing(at.astimezone(zone).minute, choices)
        if entry.timing_mode != mode:
            entry.timing_mode = mode
            entry.hard_by = None
    await db.flush()


@router.post("", response_model=SpotScheduleOut, status_code=201)
async def create_spot_schedule(
    data: SpotScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_manager),
):
    if not data.station_id:
        await _check_every_station(db, user)
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
    later = await _refresh_queues(db, {record.station_id})
    await db.commit()
    refresh_in_background(later)
    await db.refresh(record)
    return await _with_warnings(db, record)


@router.put("/{schedule_id}", response_model=SpotScheduleOut)
async def update_spot_schedule(
    schedule_id: UUID,
    data: SpotScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_manager),
):
    record = await _get_or_404(db, SpotSchedule, schedule_id, "Spot schedule")
    changes = data.model_dump(exclude_unset=True)
    # Changing an every-station schedule, or making one, needs every station
    if record.station_id is None or ("station_id" in changes and not changes["station_id"]):
        await _check_every_station(db, user)
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
    later = await _refresh_queues(db, affected)
    await db.commit()
    refresh_in_background(later)
    await db.refresh(record)
    return await _with_warnings(db, record)


@router.delete("/{schedule_id}", status_code=204)
async def delete_spot_schedule(
    schedule_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_manager),
):
    record = await _get_or_404(db, SpotSchedule, schedule_id, "Spot schedule")
    if record.station_id is None:
        await _check_every_station(db, user)
    affected = {record.station_id}
    # Turn it off first so the queue drops its slots even if it was the last
    # schedule (an empty schedule list would fall back to the legacy single spot
    # only on the next refill, leaving this sponsor's slots queued meanwhile).
    record.is_active = False
    await db.flush()
    later = await _refresh_queues(db, affected)
    if later:
        # Every station: the stations drop its slots after this answer, then it goes
        await db.commit()
        refresh_in_background(later, then_delete_schedule=record.id)
        return
    await db.delete(record)
    await db.commit()


async def _with_warnings(db: AsyncSession, record: SpotSchedule) -> SpotScheduleOut:
    """The saved sponsorship plus what does not fit because of it, said at once
    (and as one folded alert per station), not found later on Schedule Check."""
    out = SpotScheduleOut.model_validate(record)
    if not record.is_active:
        return out
    try:
        query = select(Station).where(Station.is_active.is_(True))
        if record.station_id:
            query = select(Station).where(Station.id == record.station_id)
        for station in (await db.execute(query)).scalars().all():
            lines = await room_warnings(db, station, record.id)
            out.warnings.extend(lines)
            if any("misses" in line for line in lines):
                from app.services.alert_service import create_alert

                await create_alert(
                    db,
                    "schedule_conflict",
                    "warning",
                    f"More sponsorships than room on {station.name}",
                    " ".join(lines),
                    station_id=station.id,
                )
        await db.commit()
    except Exception:
        logger.exception("Could not work out sponsorship room warnings")
        await db.rollback()
    return out


def _grouped_unplaced(unplaced, sponsors: dict) -> list[UnplacedSpot]:
    """One line per sponsorship, day, part of the day and reason, with a count."""
    grouped: dict[tuple, int] = {}
    first: dict[tuple, object] = {}
    for u in unplaced:
        key = (str(u.schedule.id), u.local_date, u.daypart, u.reason)
        grouped[key] = grouped.get(key, 0) + 1
        first.setdefault(key, u)
    return [
        UnplacedSpot(
            local_date=u.local_date.isoformat(),
            daypart=u.daypart,
            schedule_id=u.schedule.id,
            sponsor_name=sponsors.get(str(u.schedule.sponsor_id), "Unknown"),
            reason=u.reason,
            count=grouped[key],
        )
        for key, u in first.items()
    ]
