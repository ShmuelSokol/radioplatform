"""
Sponsors (clients): the record, plus the client-database views the staff
Sponsors page reads: the client list with status, one client's account
(contracts, spots through their flow), the as-run log and missed plays.
See services/sponsor_accounts.py and wiki/sponsor-accounts.md.
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.core.exceptions import ConflictError, NotFoundError
from app.models.play_log import PlayLog
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.schemas.sponsor import (
    ClientAccountOut,
    ClientPlaysOut,
    ClientSummaryOut,
    SponsorCreate,
    SponsorInDB,
    SponsorUpdate,
)
from app.schemas.sponsor_portal import MissedPlay, MissedPlaysResponse
from app.services.sponsor_accounts import client_account, client_asset_ids, client_plays, list_clients
from app.services.sponsor_delivery import PAST_DAYS, delivery_for_sponsor

router = APIRouter(prefix="/sponsors", tags=["sponsors"])


@router.get("", response_model=list[SponsorInDB])
async def list_sponsors(
    skip: int = 0,
    # Every picker on the site reads this list; at 50 the 51st client was missing
    # from all of them (there were 50+ TEST clients on 2026-11-18).
    limit: int = Query(1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Sponsor).offset(skip).limit(limit).order_by(func.lower(Sponsor.name))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=SponsorInDB, status_code=201)
async def create_sponsor(
    data: SponsorCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    record = Sponsor(**data.model_dump())
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


@router.put("/{sponsor_id}", response_model=SponsorInDB)
async def update_sponsor(
    sponsor_id: UUID,
    data: SponsorUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Sponsor).where(Sponsor.id == sponsor_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise NotFoundError("Sponsor not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{sponsor_id}", status_code=204)
async def delete_sponsor(
    sponsor_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    stmt = select(Sponsor).where(Sponsor.id == sponsor_id)
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise NotFoundError("Sponsor not found")
    # Deleting a client would take its contracts with it and leave its play history
    # nameless. A client with either is kept; end or pause its contracts instead.
    has_contracts = (
        await db.execute(select(func.count(SpotSchedule.id)).where(SpotSchedule.sponsor_id == sponsor_id))
    ).scalar()
    asset_ids = await client_asset_ids(db, record)
    has_plays = bool(asset_ids) and (
        await db.execute(select(func.count(PlayLog.id)).where(PlayLog.asset_id.in_(asset_ids)))
    ).scalar()
    if has_contracts or has_plays:
        raise ConflictError(
            "This client has contracts or play history, so it is kept. End or pause its contracts instead."
        )

    await db.delete(record)
    await db.commit()


# ── client database views ─────────────────────────────────────────


async def _sponsor_or_404(db: AsyncSession, sponsor_id: UUID) -> Sponsor:
    record = (await db.execute(select(Sponsor).where(Sponsor.id == sponsor_id))).scalar_one_or_none()
    if not record:
        raise NotFoundError("Sponsor not found")
    return record


@router.get("/clients", response_model=list[ClientSummaryOut])
async def list_client_summaries(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Every client once, with whether they are on the air and since/until when."""
    return [ClientSummaryOut.model_validate(r, from_attributes=True) for r in await list_clients(db)]


@router.get("/{sponsor_id}/account", response_model=ClientAccountOut)
async def get_client_account(
    sponsor_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """One client: details, contracts with their settings and plays, and every spot
    by where it is in the flow (wording, recorded, approved, on air, finished)."""
    sponsor = await _sponsor_or_404(db, sponsor_id)
    account = await client_account(db, sponsor)
    return ClientAccountOut(
        client=ClientSummaryOut.model_validate(account.client, from_attributes=True),
        notes=account.notes,
        contracts=[c.__dict__ for c in account.contracts],
        spots=[s.__dict__ for s in account.spots],
        stages=[{"key": k, "label": label} for k, label in account.stages],
    )


@router.get("/{sponsor_id}/plays", response_model=ClientPlaysOut)
async def get_client_plays(
    sponsor_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    station_id: UUID | None = None,
    asset_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """The client's as-run log: every airing of their audio, newest first."""
    sponsor = await _sponsor_or_404(db, sponsor_id)
    total, rows = await client_plays(
        db, sponsor, page=page, limit=limit, station_id=station_id, asset_id=asset_id,
        date_from=date_from, date_to=date_to,
    )
    return ClientPlaysOut(total=total, page=page, limit=limit, entries=rows)


@router.get("/{sponsor_id}/missed-plays", response_model=MissedPlaysResponse)
async def get_client_missed_plays(
    sponsor_id: UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """What the client's portal shows as missed: promised plays that did not air
    in the last two weeks, and coming plays with no room or moved."""
    sponsor = await _sponsor_or_404(db, sponsor_id)
    found = await delivery_for_sponsor(db, sponsor)
    rows = [
        MissedPlay(
            when=r.when, date=r.local_date.isoformat(), daypart=r.daypart,
            station_name=r.station_name, asset_title=r.asset_title, promised=r.promised,
            played=r.played, missed=r.missed, moved=r.moved, note=r.note,
        )
        for r in found
    ]
    return MissedPlaysResponse(
        days=PAST_DAYS,
        missed_past=sum(r.missed for r in rows if r.when == "past"),
        missed_coming=sum(r.missed for r in rows if r.when == "coming"),
        moved_coming=sum(r.moved for r in rows if r.when == "coming"),
        rows=rows,
    )
