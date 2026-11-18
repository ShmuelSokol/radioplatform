"""RDS: the text FM radios show for the station (PS name and RadioText).

Public feeds for the RDS encoder (Magic RDS polls a URL or reads a file):
  GET /stations/{id}/rds.txt     RadioText now, one line, at most 64 characters
  GET /stations/{id}/rds-ps.txt  PS name now, at most 8 characters
  GET /stations/{id}/rds.json    both, with what kind of line it is and when it changes
`rds.txt?mode=now_playing` is the old feed: the song on air only.

Staff screens (/rds/...): settings, the message rotation, a preview and the
log of what the encoder fetched. See wiki/rds-feed.md.
"""
import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import station_guard as sg
from app.core.dependencies import (
    get_db,
    may_operate_every_station,
    may_operate_station,
    require_manager,
    require_staff,
)
from app.models.asset import Asset
from app.models.rds import RdsMessage, RdsPlay, RdsSettings
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User
from app.services import rds_service as rds

router = APIRouter(prefix="/stations", tags=["rds"])

RADIOTEXT_MAX = rds.RADIOTEXT_MAX
_NO_STORE = {"Cache-Control": "no-store"}


def radiotext(asset: Asset | None, station_name: str, limit: int = RADIOTEXT_MAX) -> str:
    """The song-on-air line: "Artist - Title" for music and programs, the
    sponsor's line for a sponsorship, the station's name otherwise."""
    return rds.fit(rds.song_line(asset) or station_name, limit)


async def _station(db: AsyncSession, station_id: uuid.UUID) -> Station | None:
    return (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()


@router.get("/{station_id}/rds.txt", response_class=PlainTextResponse)
async def rds_text(
    station_id: uuid.UUID,
    max: int = Query(RADIOTEXT_MAX, ge=8, le=128, description="Longest line to return"),
    mode: str = Query("scheduled", pattern="^(scheduled|now_playing)$"),
    db: AsyncSession = Depends(get_db),
):
    station = await _station(db, station_id)
    if station is None:
        return PlainTextResponse("", status_code=404)
    if mode == "now_playing":
        asset = await rds.on_air_asset(db, station_id)
        return PlainTextResponse(radiotext(asset, station.name, max), headers=_NO_STORE)
    state = await rds.current(db, station)
    await rds.log_fetch(db, station_id, state)
    return PlainTextResponse(rds.fit(state.rt, max), headers=_NO_STORE)


@router.get("/{station_id}/rds-ps.txt", response_class=PlainTextResponse)
async def rds_ps(station_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    station = await _station(db, station_id)
    if station is None:
        return PlainTextResponse("", status_code=404)
    state = await rds.current(db, station)
    return PlainTextResponse(state.ps, headers=_NO_STORE)


@router.get("/{station_id}/rds.json")
async def rds_json(station_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    station = await _station(db, station_id)
    if station is None:
        return JSONResponse({"detail": "Station not found"}, status_code=404)
    state = await rds.current(db, station)
    await rds.log_fetch(db, station_id, state)
    return JSONResponse(state.as_dict(), headers=_NO_STORE)


# ── staff ───────────────────────────────────────────────────────────


async def of_rds_message(db: AsyncSession, key: uuid.UUID):
    return (await db.execute(select(RdsMessage.station_id).where(RdsMessage.id == key))).scalar_one_or_none()


admin_router = APIRouter(
    prefix="/rds",
    tags=["rds"],
    dependencies=[Depends(sg.station_write_guard(path={"station_id": sg.station, "message_id": of_rds_message}))],
)


class SettingsIn(BaseModel):
    ps_text: str | None = Field(None, max_length=8)
    ps_mode: str = Field("static", pattern="^(static|rotate)$")
    rotation_seconds: int = Field(15, ge=5, le=600)
    now_playing_weight: int = Field(2, ge=0, le=20)
    sponsor_while_spot_airs: bool = True
    fallback_text: str | None = Field(None, max_length=64)

    @field_validator("ps_text", "fallback_text")
    @classmethod
    def _plain(cls, v: str | None):
        if v is None or v.strip() == "":
            return None
        if rds.dropped_characters(v):
            raise ValueError("has characters radios cannot show (use English letters, digits and punctuation)")
        return v.strip() if len(v.strip()) else None


class MessageIn(BaseModel):
    station_id: uuid.UUID | None = None
    name: str | None = Field(None, max_length=255)
    kind: str = Field("text", pattern="^(text|sponsor|time|weather)$")
    text: str = Field(..., min_length=1, max_length=200)
    ps_text: str | None = Field(None, max_length=8)
    sponsor_id: uuid.UUID | None = None
    weight: int = Field(1, ge=1, le=20)
    days_of_week: str = "0,1,2,3,4,5,6"
    start_time: time | None = None
    end_time: time | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool = True

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v: str):
        days = sorted({d.strip() for d in v.split(",") if d.strip()})
        if not days or any(d not in {"0", "1", "2", "3", "4", "5", "6"} for d in days):
            raise ValueError("days are 0 (Monday) to 6 (Sunday), comma separated")
        return ",".join(days)

    @field_validator("ps_text")
    @classmethod
    def _ps(cls, v: str | None):
        if v is None or v.strip() == "":
            return None
        if rds.dropped_characters(v):
            raise ValueError("has characters radios cannot show")
        return v


class MessagePatch(MessageIn):
    text: str | None = Field(None, min_length=1, max_length=200)  # type: ignore[assignment]
    kind: str | None = Field(None, pattern="^(text|sponsor|time|weather)$")  # type: ignore[assignment]


class CheckIn(BaseModel):
    text: str = ""
    ps_text: str | None = None
    kind: str = "text"
    sponsor_id: uuid.UUID | None = None


def _message_out(m: RdsMessage, sponsors: dict) -> dict:
    sid = str(m.sponsor_id) if m.sponsor_id else None
    return {
        "id": str(m.id),
        "station_id": str(m.station_id) if m.station_id else None,
        "name": m.name,
        "kind": m.kind,
        "text": m.text,
        "ps_text": m.ps_text,
        "sponsor_id": sid,
        "sponsor_name": sponsors.get(sid) if sid else None,
        "weight": m.weight,
        "days_of_week": m.days_of_week,
        "start_time": m.start_time.strftime("%H:%M") if m.start_time else None,
        "end_time": m.end_time.strftime("%H:%M") if m.end_time else None,
        "start_date": m.start_date.isoformat() if m.start_date else None,
        "end_date": m.end_date.isoformat() if m.end_date else None,
        "is_active": m.is_active,
        "check": rds.check_text(m.text, ps_text=m.ps_text, kind=m.kind, sponsor_name=sponsors.get(sid) if sid else None),
    }


async def _sponsor_names(db: AsyncSession) -> dict[str, str]:
    return {str(i): n for i, n in (await db.execute(select(Sponsor.id, Sponsor.name))).all()}


async def _require_station(db: AsyncSession, station_id: uuid.UUID) -> Station:
    station = await _station(db, station_id)
    if station is None:
        raise HTTPException(404, "Station not found")
    return station


@admin_router.get("/stations/{station_id}/settings")
async def get_settings(station_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_staff)):
    station = await _require_station(db, station_id)
    s = await rds.settings_for(db, station_id)
    return {**s, "station_id": str(station_id), "default_ps": rds.default_ps(station.name),
            "default_fallback": rds.fit(station.name)}


@admin_router.put("/stations/{station_id}/settings")
async def put_settings(
    station_id: uuid.UUID, body: SettingsIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_manager)
):
    await _require_station(db, station_id)
    row = (await db.execute(select(RdsSettings).where(RdsSettings.station_id == station_id))).scalar_one_or_none()
    if row is None:
        row = RdsSettings(id=uuid.uuid4(), station_id=station_id)
        db.add(row)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await db.commit()
    return await get_settings(station_id, db, _)


@admin_router.get("/messages")
async def list_messages(
    station_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db), _: User = Depends(require_staff)
):
    q = select(RdsMessage).order_by(RdsMessage.created_at, RdsMessage.id)
    if station_id is not None:
        rows = await rds.messages_for(db, station_id)
    else:
        rows = (await db.execute(q)).scalars().all()
    sponsors = await _sponsor_names(db)
    return {"messages": [_message_out(m, sponsors) for m in rows]}


async def _validate(db: AsyncSession, user: User, data: dict) -> None:
    if data.get("station_id") is None and not await may_operate_every_station(db, user):
        raise HTTPException(403, "A line for every station needs a login allowed on every station")
    if data.get("station_id") is not None:
        station = await _require_station(db, data["station_id"])
        if not may_operate_station(user, station):
            raise HTTPException(403, "Your login is not set up to operate this station")
    if data.get("kind") == "sponsor" and not data.get("sponsor_id"):
        raise HTTPException(422, "Choose the sponsor this line is for")
    if data.get("sponsor_id") and await db.get(Sponsor, data["sponsor_id"]) is None:
        raise HTTPException(422, "Sponsor not found")
    if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
        raise HTTPException(422, "The end date is before the start date")
    check = rds.check_text(data.get("text") or "", ps_text=data.get("ps_text"), kind=data.get("kind") or "text")
    if not check["ok"]:
        raise HTTPException(422, " ".join(check["errors"]))


@admin_router.post("/messages", status_code=201)
async def create_message(body: MessageIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    data = body.model_dump()
    await _validate(db, user, data)
    m = RdsMessage(id=uuid.uuid4(), **data)
    db.add(m)
    await db.commit()
    return _message_out(m, await _sponsor_names(db))


@admin_router.patch("/messages/{message_id}")
async def update_message(
    message_id: uuid.UUID, body: MessagePatch, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)
):
    m = await db.get(RdsMessage, message_id)
    if m is None:
        raise HTTPException(404, "RDS line not found")
    changes = body.model_dump(exclude_unset=True)
    merged = {c: getattr(m, c) for c in MessageIn.model_fields} | changes
    await _validate(db, user, merged)
    for k, v in changes.items():
        setattr(m, k, v)
    await db.commit()
    return _message_out(m, await _sponsor_names(db))


@admin_router.delete("/messages/{message_id}", status_code=204)
async def delete_message(message_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    m = await db.get(RdsMessage, message_id)
    if m is None:
        raise HTTPException(404, "RDS line not found")
    if m.station_id is None and not await may_operate_every_station(db, user):
        raise HTTPException(403, "A line for every station needs a login allowed on every station")
    await db.delete(m)
    await db.commit()


@admin_router.post("/check")
async def check(body: CheckIn, db: AsyncSession = Depends(get_db), _: User = Depends(require_staff)):
    sponsor_name = None
    if body.sponsor_id:
        sp = await db.get(Sponsor, body.sponsor_id)
        sponsor_name = sp.name if sp else None
    return rds.check_text(body.text, ps_text=body.ps_text, kind=body.kind, sponsor_name=sponsor_name)


@admin_router.get("/stations/{station_id}/preview")
async def preview(
    station_id: uuid.UUID,
    minutes: int = Query(10, ge=1, le=120),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    station = await _require_station(db, station_id)
    rows = await rds.preview(db, station, minutes)
    return {"now": rows[0], "coming": rows[1:]}


@admin_router.get("/plays")
async def plays(
    station_id: uuid.UUID | None = None,
    sponsor_id: uuid.UUID | None = None,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    since = datetime.now(UTC) - timedelta(days=days)
    q = select(RdsPlay).where(RdsPlay.first_seen_at >= since)
    if station_id:
        q = q.where(RdsPlay.station_id == station_id)
    if sponsor_id:
        q = q.where(RdsPlay.sponsor_id == sponsor_id)
    rows = (await db.execute(q.order_by(RdsPlay.first_seen_at.desc()).limit(limit))).scalars().all()
    sponsors = await _sponsor_names(db)
    totals: dict[str, dict] = {}
    for p in rows:
        if p.sponsor_id:
            sid = str(p.sponsor_id)
            t = totals.setdefault(sid, {"sponsor_id": sid, "sponsor_name": sponsors.get(sid), "plays": 0, "seconds": 0})
            t["plays"] += 1
            t["seconds"] += _seconds(p)
    return {
        "plays": [
            {
                "id": str(p.id),
                "station_id": str(p.station_id),
                "kind": p.kind,
                "text": p.text,
                "ps_text": p.ps_text,
                "message_id": str(p.message_id) if p.message_id else None,
                "sponsor_id": str(p.sponsor_id) if p.sponsor_id else None,
                "sponsor_name": sponsors.get(str(p.sponsor_id)) if p.sponsor_id else None,
                "first_seen_at": _iso(p.first_seen_at),
                "last_seen_at": _iso(p.last_seen_at),
                "fetches": p.fetches,
            }
            for p in rows
        ],
        "sponsor_totals": sorted(totals.values(), key=lambda t: -t["plays"]),
    }


def _iso(d: datetime) -> str:
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()


def _seconds(p: RdsPlay) -> int:
    a = p.first_seen_at if p.first_seen_at.tzinfo else p.first_seen_at.replace(tzinfo=UTC)
    b = p.last_seen_at if p.last_seen_at.tzinfo else p.last_seen_at.replace(tzinfo=UTC)
    return max(0, int((b - a).total_seconds()))
