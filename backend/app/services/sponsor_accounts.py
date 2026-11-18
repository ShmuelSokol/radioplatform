"""Sponsors as clients: the account view behind the staff Sponsors page.

Modelled on radio traffic systems (WideOrbit, Marketron, NaturalLog): a client
(advertiser) has orders (here: contracts = SpotSchedule rows: station, days,
times, dates), copy (here: spots = the recordings and library audio tied to the
client) moving through a production flow, and an as-run log (the play log) that
proves what aired and when.

Nothing here writes. Status is worked out on each read from the contracts' dates
and switches, so it can never disagree with what the planner airs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_campaign import AdCampaign
from app.models.asset import NEVER_AUTO_TITLES, Asset
from app.models.play_log import PlayLog
from app.models.sponsor import Sponsor
from app.models.spot_recording import SpotRecording
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.spot_schedule_service import as_utc, station_tz

DEFAULT_TZ = "America/New_York"

# Contract states, in the order a client's own status is taken from them.
RUNNING, UPCOMING, PAUSED, ENDED = "running", "upcoming", "paused", "ended"
CLIENT_ORDER = [RUNNING, UPCOMING, PAUSED, ENDED]

# The flow of a spot from first wording to finished, as the client page shows it.
STAGES = [
    ("wording", "Wording not approved yet"),
    ("approved_not_recorded", "Wording approved, not recorded yet"),
    ("recorded_needs_approval", "Recorded, waiting for approval"),
    ("ready_not_scheduled", "Approved, not in a contract"),
    ("starts_soon", "Scheduled to start"),
    ("on_air", "On the air"),
    ("paused", "Paused"),
    ("finished", "Finished"),
    ("rejected", "Rejected or taken off the air"),
]
STAGE_LABEL = dict(STAGES)


def contract_state(schedule: SpotSchedule, today: date) -> str:
    if schedule.end_date and schedule.end_date < today:
        return ENDED
    if not schedule.is_active:
        return PAUSED
    if schedule.start_date and schedule.start_date > today:
        return UPCOMING
    return RUNNING


def _off_air_reason(asset: Asset | None) -> str | None:
    """Why this audio never airs, whatever its contract says (the planner skips it)."""
    if asset is None:
        return "Its recording is no longer in the library"
    if (asset.category or "").lower() == "do_not_play":
        return "Its recording is marked do not play"
    if (asset.title or "").strip().lower() in NEVER_AUTO_TITLES:
        return "Its recording is a placeholder"
    if (asset.review_status or "") == "rejected":
        return "Its recording was rejected"
    return None


@dataclass
class _Ctx:
    stations: dict[str, Station]
    default_tz: ZoneInfo
    now: datetime

    def tz_for(self, station_id) -> ZoneInfo:
        station = self.stations.get(str(station_id)) if station_id else None
        return station_tz(station.timezone) if station else self.default_tz

    def today(self, station_id=None) -> date:
        return self.now.astimezone(self.tz_for(station_id)).date()

    def station_name(self, station_id) -> str:
        if not station_id:
            return "Every station"
        station = self.stations.get(str(station_id))
        return station.name if station else "Removed station"


async def _ctx(db: AsyncSession, now: datetime | None) -> _Ctx:
    stations = (await db.execute(select(Station))).scalars().all()
    active = [s for s in stations if s.is_active]
    default = station_tz(active[0].timezone if active else DEFAULT_TZ)
    # Main's clock is the station's own; prefer it when there is one
    for s in active:
        if "main" in (s.name or "").lower():
            default = station_tz(s.timezone)
            break
    return _Ctx({str(s.id): s for s in stations}, default, now or datetime.now(UTC))


def _client_status(states: list[str]) -> str:
    for state in CLIENT_ORDER:
        if state in states:
            return state
    return "none"


def _active_span(schedules: list[SpotSchedule], ctx: _Ctx) -> tuple[date | None, date | None, bool]:
    """First contract day, last contract day, and whether a contract has no end."""
    if not schedules:
        return None, None, False
    starts = []
    for s in schedules:
        if s.start_date:
            starts.append(s.start_date)
        elif s.created_at:
            starts.append(as_utc(s.created_at).astimezone(ctx.tz_for(s.station_id)).date())
    open_ended = any(s.end_date is None for s in schedules)
    ends = [s.end_date for s in schedules if s.end_date]
    return (min(starts) if starts else None), (max(ends) if ends else None), open_ended


# ── the list of clients ─────────────────────────────────────────────


@dataclass
class ClientRow:
    id: str
    name: str
    company_name: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    status: str
    active_from: date | None
    active_until: date | None
    open_ended: bool
    contracts_running: int
    contracts_total: int
    spots_on_air: int
    spots_in_progress: int
    plays_7_days: int
    plays_total: int
    last_played_at: datetime | None
    has_portal_login: bool
    created_at: datetime | None


async def _asset_owner_map(db: AsyncSession, sponsor_ids: set[str] | None = None) -> dict[str, set[str]]:
    """sponsor id -> ids of the library audio that belongs to them."""
    owned: dict[str, set[str]] = {}
    q = select(SpotSchedule.sponsor_id, SpotSchedule.asset_id)
    if sponsor_ids is not None:
        q = q.where(SpotSchedule.sponsor_id.in_(sponsor_ids))
    for sponsor_id, asset_id in (await db.execute(q)).all():
        owned.setdefault(str(sponsor_id), set()).add(str(asset_id))
    q = select(Asset.sponsor_id, Asset.id).where(Asset.sponsor_id.isnot(None))
    if sponsor_ids is not None:
        q = q.where(Asset.sponsor_id.in_(sponsor_ids))
    for sponsor_id, asset_id in (await db.execute(q)).all():
        owned.setdefault(str(sponsor_id), set()).add(str(asset_id))
    q = select(SpotRecording.sponsor_id, SpotRecording.asset_id).where(SpotRecording.asset_id.isnot(None))
    if sponsor_ids is not None:
        q = q.where(SpotRecording.sponsor_id.in_(sponsor_ids))
    for sponsor_id, asset_id in (await db.execute(q)).all():
        owned.setdefault(str(sponsor_id), set()).add(str(asset_id))
    return owned


async def list_clients(db: AsyncSession, now: datetime | None = None) -> list[ClientRow]:
    ctx = await _ctx(db, now)
    sponsors = (await db.execute(select(Sponsor).order_by(func.lower(Sponsor.name)))).scalars().all()
    schedules = (await db.execute(select(SpotSchedule))).scalars().all()
    by_sponsor: dict[str, list[SpotSchedule]] = {}
    for s in schedules:
        by_sponsor.setdefault(str(s.sponsor_id), []).append(s)

    owned = await _asset_owner_map(db)
    all_assets = {a for ids in owned.values() for a in ids}
    week_ago = ctx.now - timedelta(days=7)
    plays: dict[str, tuple[int, int, datetime | None]] = {}
    if all_assets:
        rows = await db.execute(
            select(
                PlayLog.asset_id,
                func.count(PlayLog.id),
                func.sum(case((PlayLog.start_utc >= week_ago, 1), else_=0)),
                func.max(PlayLog.start_utc),
            )
            .where(PlayLog.asset_id.in_([uuid.UUID(a) for a in all_assets]))
            .group_by(PlayLog.asset_id)
        )
        for asset_id, total, week, last in rows.all():
            plays[str(asset_id)] = (int(total or 0), int(week or 0), as_utc(last) if last else None)

    assets = {}
    if all_assets:
        for a in (
            await db.execute(select(Asset).where(Asset.id.in_([uuid.UUID(x) for x in all_assets])))
        ).scalars():
            assets[str(a.id)] = a

    in_progress: dict[str, int] = {}
    for sponsor_id, n in (
        await db.execute(
            select(SpotRecording.sponsor_id, func.count(SpotRecording.id))
            .where(SpotRecording.status.in_(("draft", "ready")))
            .group_by(SpotRecording.sponsor_id)
        )
    ).all():
        in_progress[str(sponsor_id)] = int(n)
    for sponsor_id, n in (
        await db.execute(
            select(AdCampaign.sponsor_id, func.count(AdCampaign.id))
            .where(AdCampaign.status.in_(("draft", "in_review", "approved")))
            .group_by(AdCampaign.sponsor_id)
        )
    ).all():
        in_progress[str(sponsor_id)] = in_progress.get(str(sponsor_id), 0) + int(n)

    out: list[ClientRow] = []
    for sp in sponsors:
        key = str(sp.id)
        mine = by_sponsor.get(key, [])
        states = [contract_state(s, ctx.today(s.station_id)) for s in mine]
        start, end, open_ended = _active_span(mine, ctx)
        on_air_assets = {
            str(s.asset_id)
            for s, st in zip(mine, states, strict=True)
            if st == RUNNING and _off_air_reason(assets.get(str(s.asset_id))) is None
        }
        mine_assets = owned.get(key, set())
        total = sum(plays.get(a, (0, 0, None))[0] for a in mine_assets)
        week = sum(plays.get(a, (0, 0, None))[1] for a in mine_assets)
        lasts = [plays[a][2] for a in mine_assets if a in plays and plays[a][2]]
        out.append(
            ClientRow(
                id=key,
                name=sp.name,
                company_name=sp.company_name,
                contact_name=sp.contact_name,
                contact_email=sp.contact_email,
                contact_phone=sp.contact_phone,
                status=_client_status(states),
                active_from=start,
                active_until=end,
                open_ended=open_ended,
                contracts_running=states.count(RUNNING),
                contracts_total=len(mine),
                spots_on_air=len(on_air_assets),
                spots_in_progress=in_progress.get(key, 0),
                plays_7_days=week,
                plays_total=total,
                last_played_at=max(lasts) if lasts else None,
                has_portal_login=sp.user_id is not None,
                created_at=as_utc(sp.created_at) if sp.created_at else None,
            )
        )
    return out


# ── one client's account ────────────────────────────────────────────


@dataclass
class ContractRow:
    id: str
    name: str | None
    station_id: str | None
    station_name: str
    asset_id: str
    asset_title: str
    duration: float | None
    days_of_week: str
    dayparts: str
    plays_per_daypart: int
    fixed_times: str | None
    timing_mode: str | None
    start_date: date | None
    end_date: date | None
    is_active: bool
    state: str
    problem: str | None
    plays_in_contract: int
    plays_7_days: int
    last_played_at: datetime | None
    created_at: datetime | None


@dataclass
class SpotRow:
    key: str
    kind: str  # "audio" (library item), "recording" (in Record Spots), "campaign" (portal request)
    stage: str
    stage_label: str
    title: str
    script: str | None = None
    duration: float | None = None
    asset_id: str | None = None
    recording_id: str | None = None
    campaign_id: str | None = None
    status_detail: str | None = None
    contract_ids: list[str] = field(default_factory=list)
    plays_total: int = 0
    last_played_at: datetime | None = None
    first_played_at: datetime | None = None
    updated_at: datetime | None = None


def _asset_stage(states: list[str], off_air: str | None) -> str:
    if off_air:
        return "rejected"
    if not states:
        return "ready_not_scheduled"
    if RUNNING in states:
        return "on_air"
    if UPCOMING in states:
        return "starts_soon"
    if PAUSED in states:
        return "paused"
    return "finished"


def _recording_stage(rec: SpotRecording) -> tuple[str, str]:
    if rec.status == "rejected":
        return "rejected", "Recording rejected"
    if rec.status == "ready":
        return "recorded_needs_approval", "Audio made, listen and approve in Record Spots"
    verdict = (rec.review or {}).get("verdict")
    if verdict == "ok":
        return "approved_not_recorded", "Wording passed the check, no audio yet"
    if verdict == "needs_changes":
        return "wording", "Wording check asks for changes"
    return "wording", "Script written, not checked yet"


CAMPAIGN_STAGE = {
    "draft": ("wording", "Request started in the sponsor portal"),
    "in_review": ("wording", "Wording sent for review"),
    "approved": ("approved_not_recorded", "Wording approved, waiting for a recording"),
    "active": ("on_air", "Campaign marked active"),
    "paused": ("paused", "Campaign paused"),
    "completed": ("finished", "Campaign completed"),
}


@dataclass
class Account:
    client: ClientRow
    notes: str | None
    contracts: list[ContractRow]
    spots: list[SpotRow]
    stages: list[tuple[str, str]]


async def client_account(db: AsyncSession, sponsor: Sponsor, now: datetime | None = None) -> Account:
    ctx = await _ctx(db, now)
    rows = await list_clients(db, now=ctx.now)
    client = next(r for r in rows if r.id == str(sponsor.id))

    schedules = (
        (await db.execute(select(SpotSchedule).where(SpotSchedule.sponsor_id == sponsor.id)))
        .scalars()
        .all()
    )
    recordings = (
        (
            await db.execute(
                select(SpotRecording)
                .where(SpotRecording.sponsor_id == sponsor.id)
                .order_by(SpotRecording.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    campaigns = (
        (
            await db.execute(
                select(AdCampaign)
                .where(AdCampaign.sponsor_id == sponsor.id)
                .order_by(AdCampaign.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    owned = (await _asset_owner_map(db, {sponsor.id})).get(str(sponsor.id), set())
    asset_ids = [uuid.UUID(a) for a in owned]
    assets = {}
    if asset_ids:
        for a in (await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars():
            assets[str(a.id)] = a

    plays: list[tuple[str, str, datetime]] = []
    if asset_ids:
        plays = [
            (str(st), str(a), as_utc(at))
            for st, a, at in (
                await db.execute(
                    select(PlayLog.station_id, PlayLog.asset_id, PlayLog.start_utc).where(
                        PlayLog.asset_id.in_(asset_ids)
                    )
                )
            ).all()
        ]
    week_ago = ctx.now - timedelta(days=7)

    contracts: list[ContractRow] = []
    states_by_asset: dict[str, list[str]] = {}
    contracts_by_asset: dict[str, list[str]] = {}
    for s in schedules:
        tz = ctx.tz_for(s.station_id)
        state = contract_state(s, ctx.today(s.station_id))
        asset = assets.get(str(s.asset_id))
        off = _off_air_reason(asset)
        if state == ENDED:
            off = None  # nothing to warn about once a contract is over
        lo = (
            datetime(s.start_date.year, s.start_date.month, s.start_date.day, tzinfo=tz).astimezone(UTC)
            if s.start_date
            else None
        )
        hi = None
        if s.end_date:
            nxt = s.end_date + timedelta(days=1)
            hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC)
        mine = [
            at
            for st, a, at in plays
            if a == str(s.asset_id)
            and (s.station_id is None or st == str(s.station_id))
            and (lo is None or at >= lo)
            and (hi is None or at < hi)
        ]
        contracts.append(
            ContractRow(
                id=str(s.id),
                name=s.name,
                station_id=str(s.station_id) if s.station_id else None,
                station_name=ctx.station_name(s.station_id),
                asset_id=str(s.asset_id),
                asset_title=asset.title if asset else "Removed recording",
                duration=asset.duration if asset else None,
                days_of_week=s.days_of_week,
                dayparts=s.dayparts,
                plays_per_daypart=s.plays_per_daypart,
                fixed_times=s.fixed_times,
                timing_mode=s.timing_mode,
                start_date=s.start_date,
                end_date=s.end_date,
                is_active=s.is_active,
                state=state,
                problem=off if state in (RUNNING, UPCOMING) else None,
                plays_in_contract=len(mine),
                plays_7_days=sum(1 for at in mine if at >= week_ago),
                last_played_at=max(mine) if mine else None,
                created_at=as_utc(s.created_at) if s.created_at else None,
            )
        )
        states_by_asset.setdefault(str(s.asset_id), []).append(state)
        contracts_by_asset.setdefault(str(s.asset_id), []).append(str(s.id))

    state_rank = {RUNNING: 0, UPCOMING: 1, PAUSED: 2, ENDED: 3}
    contracts.sort(
        key=lambda c: (state_rank[c.state], -(c.start_date or date.min).toordinal(), c.asset_title.lower())
    )

    script_of: dict[str, SpotRecording] = {str(r.asset_id): r for r in recordings if r.asset_id}
    spots: list[SpotRow] = []
    for key, asset in assets.items():
        aired = [at for _, a, at in plays if a == key]
        stage = _asset_stage(states_by_asset.get(key, []), _off_air_reason(asset))
        rec = script_of.get(key)
        detail = _off_air_reason(asset) if stage == "rejected" else None
        spots.append(
            SpotRow(
                key=f"asset:{key}",
                kind="audio",
                stage=stage,
                stage_label=STAGE_LABEL[stage],
                title=asset.title,
                script=rec.script_text if rec else None,
                duration=asset.duration,
                asset_id=key,
                recording_id=str(rec.id) if rec else None,
                status_detail=detail,
                contract_ids=contracts_by_asset.get(key, []),
                plays_total=len(aired),
                last_played_at=max(aired) if aired else None,
                first_played_at=min(aired) if aired else None,
                updated_at=as_utc(asset.updated_at) if asset.updated_at else None,
            )
        )
    for rec in recordings:
        if rec.asset_id and str(rec.asset_id) in assets:
            continue  # shown as its library audio above
        stage, detail = _recording_stage(rec)
        if rec.status == "approved":  # approved, but its library item is gone
            stage, detail = "rejected", "Approved, but its library item was removed"
        spots.append(
            SpotRow(
                key=f"recording:{rec.id}",
                kind="recording",
                stage=stage,
                stage_label=STAGE_LABEL[stage],
                title=rec.title,
                script=rec.script_text,
                duration=rec.duration,
                recording_id=str(rec.id),
                status_detail=detail,
                updated_at=as_utc(rec.updated_at) if rec.updated_at else None,
            )
        )
    for c in campaigns:
        status = c.status.value if hasattr(c.status, "value") else str(c.status)
        stage, detail = CAMPAIGN_STAGE.get(status, ("wording", status))
        spots.append(
            SpotRow(
                key=f"campaign:{c.id}",
                kind="campaign",
                stage=stage,
                stage_label=STAGE_LABEL[stage],
                title=c.name,
                script=c.description,
                campaign_id=str(c.id),
                status_detail=detail,
                updated_at=as_utc(c.updated_at) if c.updated_at else None,
            )
        )
    order = {k: i for i, (k, _) in enumerate(STAGES)}
    spots.sort(key=lambda s: (order[s.stage], s.title.lower()))
    return Account(client=client, notes=sponsor.notes, contracts=contracts, spots=spots, stages=STAGES)


# ── the as-run log (what aired, when) ───────────────────────────────


async def client_asset_ids(db: AsyncSession, sponsor: Sponsor) -> list[uuid.UUID]:
    owned = (await _asset_owner_map(db, {sponsor.id})).get(str(sponsor.id), set())
    if sponsor.audio_file_path:
        for (aid,) in (
            await db.execute(select(Asset.id).where(Asset.file_path == sponsor.audio_file_path))
        ).all():
            owned.add(str(aid))
    return [uuid.UUID(a) for a in owned]


async def client_plays(
    db: AsyncSession,
    sponsor: Sponsor,
    *,
    page: int = 1,
    limit: int = 50,
    station_id: uuid.UUID | None = None,
    asset_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> tuple[int, list[dict]]:
    ctx = await _ctx(db, now)
    ids = await client_asset_ids(db, sponsor)
    if asset_id is not None:
        ids = [a for a in ids if a == asset_id]
    if not ids:
        return 0, []
    conds = [PlayLog.asset_id.in_(ids)]
    if station_id is not None:
        conds.append(PlayLog.station_id == station_id)
    tz = ctx.tz_for(station_id)
    if date_from:
        conds.append(
            PlayLog.start_utc >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=tz).astimezone(UTC)
        )
    if date_to:
        nxt = date_to + timedelta(days=1)
        conds.append(PlayLog.start_utc < datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC))
    total = (await db.execute(select(func.count(PlayLog.id)).where(*conds))).scalar() or 0
    result = await db.execute(
        select(PlayLog, Asset.title, Asset.duration)
        .outerjoin(Asset, PlayLog.asset_id == Asset.id)
        .where(*conds)
        .order_by(PlayLog.start_utc.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    out = []
    for log, title, duration in result.all():
        start = as_utc(log.start_utc)
        end = as_utc(log.end_utc) if log.end_utc else None
        local = start.astimezone(ctx.tz_for(log.station_id))
        source = log.source.value if hasattr(log.source, "value") else str(log.source)
        out.append(
            {
                "id": str(log.id),
                "station_id": str(log.station_id),
                "station_name": ctx.station_name(log.station_id),
                "asset_id": str(log.asset_id) if log.asset_id else None,
                "asset_title": title or "Sponsorship",
                "start_utc": start,
                "end_utc": end,
                "local_date": local.date().isoformat(),
                "local_time": local.strftime("%I:%M:%S %p").lstrip("0"),
                "aired_seconds": (end - start).total_seconds() if end else None,
                "length_seconds": duration,
                "how": {
                    "ad": "Sponsorship slot",
                    "scheduler": "Queue",
                    "manual": "Played by hand",
                    "fallback": "Fill-in",
                }.get(source, source),
            }
        )
    return int(total), out

