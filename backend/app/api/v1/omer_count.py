"""Sefiras HaOmer clips and what each station will say (services/omer_count.py).

GET  /omer-count                       the 49 scripts, which are recorded, and the dates of the
                                       count running or coming next
GET  /omer-count/station/{station_id}  one station's setting and what it says each hour of a day
PUT  /omer-count/{day}                 upload the recording for one day (manager)
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import may_operate_station, require_manager, require_staff
from app.db.session import get_db
from app.models.station import Station
from app.models.user import User
from app.services import omer_count as omer

router = APIRouter(prefix="/omer-count", tags=["omer-count"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


class OmerClip(BaseModel):
    day: int
    text: str
    file: str
    recorded: bool
    asset_id: str | None = None
    file_url: str | None = None
    duration: float | None = None
    recorded_text: str | None = None


class OmerOverview(BaseModel):
    first_day: date  # civil date of Omer day 1 (16 Nisan) of the count running or next
    last_day: date  # Erev Shavuos (day 49)
    recorded: int
    total: int
    clips: list[OmerClip]


class OmerHour(BaseModel):
    at: datetime  # UTC
    local: str  # "8:00 PM"
    omer_day: int | None
    text: str | None
    recorded: bool
    off_air: str | None = None  # name of the Shabbos / Yom Tov window covering it


class StationOmer(BaseModel):
    station_id: str
    enabled: bool
    switch_minutes: int
    place_known: bool
    date: date
    sunrise: str | None
    shekia: str | None
    switch_at: str | None
    hours: list[OmerHour]


def _clip_row(n: int, asset) -> OmerClip:
    meta = asset.metadata_extra if asset is not None and isinstance(asset.metadata_extra, dict) else {}
    return OmerClip(
        day=n,
        text=omer.wording(n),
        file=f"omer-{n:02d}.mp3",
        recorded=asset is not None,
        asset_id=str(asset.id) if asset is not None else None,
        file_url=asset.file_path if asset is not None else None,
        duration=asset.duration if asset is not None else None,
        recorded_text=meta.get("script"),
    )


@router.get("", response_model=OmerOverview)
async def omer_overview(db: AsyncSession = Depends(get_db), _user: User = Depends(require_staff)):
    clips = await omer.load_clips(db)
    first, last = omer.next_count(datetime.now(timezone.utc).date())
    rows = [_clip_row(n, clips.get(n)) for n in range(1, omer.DAYS + 1)]
    return OmerOverview(first_day=first, last_day=last, recorded=sum(r.recorded for r in rows),
                        total=len(rows), clips=rows)


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


@router.get("/station/{station_id}", response_model=StationOmer)
async def station_omer(
    station_id: str,
    on: date | None = Query(None, description="The station's local date (default: today)"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    """What the station says at each hh:00 of one local date, by the same rule the queue uses."""
    from app.services.queue_replenish_service import local_hour_marks
    from app.services.spot_schedule_service import load_blackouts
    from app.services.weather_spot_service import announcement_zone

    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(404, "Station not found")
    zone = announcement_zone(station.timezone)
    setting = omer.omer_setting(station.automation_config)
    place = omer.station_place(station.latitude, station.longitude, zone.key)
    day = on or datetime.now(timezone.utc).astimezone(zone).date()
    midnight = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
    marks = [m for m in local_hour_marks(midnight - timedelta(seconds=1), zone, 25)
             if m.astimezone(zone).date() == day]
    clips = await omer.load_clips(db)
    blackouts = await load_blackouts(db, station.id, midnight - timedelta(hours=1), midnight + timedelta(hours=26))
    sunrise = shekia = switch = None
    if place is not None:
        rise, sets = omer._sun(place, zone, day)
        sunrise, shekia = _clock(rise), _clock(sets)
        switch = _clock(sets + timedelta(minutes=setting.switch_minutes))
    hours = []
    for mark in marks:
        said = omer.omer_at(mark, zone, place, setting.switch_minutes) if place is not None else None
        cover = next((b for b in blackouts if b.covers(mark)), None)
        hours.append(OmerHour(
            at=mark,
            local=_clock(mark.astimezone(zone)),
            omer_day=said,
            text=omer.wording(said) if said else None,
            recorded=bool(said and said in clips),
            off_air=cover.name if cover else None,
        ))
    return StationOmer(station_id=str(station.id), enabled=setting.enabled, switch_minutes=setting.switch_minutes,
                       place_known=place is not None, date=day, sunrise=sunrise, shekia=shekia, switch_at=switch,
                       hours=hours)


@router.put("/{day}", response_model=OmerClip)
async def upload_omer_clip(
    day: int,
    file: UploadFile = File(...),
    script: str | None = Form(None),
    source: str = Form("shprach"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Upload the recording for one Omer day (1-49), "Today is the eighth day, ...",
    played from shekia of the evening before until shekia. Every station plays it, so
    the login must be allowed on all of them. Replaces an earlier recording, including
    copies already queued."""
    if not 1 <= day <= omer.DAYS:
        raise HTTPException(422, "day must be 1-49")
    if not re.fullmatch(r"[a-z0-9-]{1,30}", source):
        raise HTTPException(422, "source must be lowercase letters, digits or dashes")
    if user.role.value != "admin":
        stations = (await db.execute(select(Station).where(Station.is_active == True))).scalars().all()  # noqa: E712
        refused = sorted(st.name for st in stations if not may_operate_station(user, st))
        if refused:
            raise HTTPException(403, f"Your login is not set up to operate {', '.join(refused)}")
    data = await file.read()
    if not data:
        raise HTTPException(422, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    try:
        asset, _ = await omer.store_uploaded_clip(db, day, data, source=source, script=script or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _clip_row(day, asset)
