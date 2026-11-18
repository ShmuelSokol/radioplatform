"""Emergency alerts: the override that takes a station's air (wiki/emergency-alerts.md).

  POST /emergency/device/activate   the EAS box or the station's tool, emergency token only
  POST /emergency/activate          staff (manager): a spoken test, never EAS tones
  POST /emergency/{id}/stop         staff (manager) or the emergency token
  GET  /emergency                   staff: the log, newest first
  GET  /emergency/status            staff: what is on air now, per station, and the setup
"""

from __future__ import annotations

import base64
import os
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_db, require_manager, require_staff
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.emergency_alert import EmergencyAlert
from app.models.station import Station
from app.models.user import User
from app.services import eas_tones, emergency_alerts

router = APIRouter(prefix="/emergency", tags=["emergency"])

KINDS = ("alert", "test", "rmt", "rwt")
TONES_REFUSED = (
    "This recording holds EAS tones (the attention signal or the SAME data bursts). "
    "Airing them outside a real alert or an authorised test breaks FCC rule 47 CFR 11.45, "
    "on the air and on a stream. Use a plain spoken clip to test the override; real alerts "
    "come from the station's EAS box."
)


def _device_token(
    x_emergency_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    """The emergency token in X-Emergency-Token, or as the password of HTTP Basic auth
    (many EAS boxes can only send a user name and password)."""
    presented = x_emergency_token
    if not presented and authorization and authorization.lower().startswith("basic "):
        try:
            presented = base64.b64decode(authorization[6:]).decode().split(":", 1)[1]
        except Exception:
            presented = None
    if not emergency_alerts.token_ok(presented):
        raise ForbiddenError("Invalid emergency token")
    return "device"


async def _stations(db: AsyncSession, station_ids: str | None) -> list:
    """Comma-separated ids, or empty / "all" for every active station."""
    if not station_ids or station_ids.strip().lower() == "all":
        rows = (await db.execute(select(Station.id).where(Station.is_active.is_(True)))).scalars().all()
        return list(rows)
    out = []
    for raw in station_ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            sid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Not a station id: {raw}") from None
        if await db.get(Station, sid) is None:
            raise NotFoundError(f"Station {raw} not found")
        out.append(sid)
    if not out:
        raise HTTPException(status_code=422, detail="Name at least one station")
    return out


async def _audio(file: UploadFile | None, audio_url: str | None) -> tuple[bytes, str | None, str | None]:
    if file is not None:
        data = await file.read(emergency_alerts.MAX_AUDIO_BYTES + 1)
        name, ctype = file.filename, file.content_type
    elif audio_url:
        if not audio_url.startswith(("https://", "http://")):
            raise HTTPException(status_code=422, detail="audio_url must be an http(s) address")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(audio_url)
            resp.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"The audio could not be fetched: {e}") from None
        data, name, ctype = resp.content, audio_url.rsplit("/", 1)[-1], resp.headers.get("content-type")
    else:
        raise HTTPException(status_code=422, detail="Send the alert's audio as `file`, or its address as `audio_url`")
    if not data:
        raise HTTPException(status_code=422, detail="The audio is empty")
    if len(data) > emergency_alerts.MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="The audio is too large (30 MB at most)")
    seconds = emergency_alerts.duration_of(data)
    if seconds is None:
        raise HTTPException(status_code=422, detail="The audio could not be read")
    if seconds > emergency_alerts.MAX_SECONDS:
        raise HTTPException(status_code=422, detail="The audio is longer than 15 minutes")
    return data, name, ctype


def _row(a: EmergencyAlert, names: dict) -> dict:
    return {
        "id": str(a.id), "station_id": str(a.station_id) if a.station_id else None,
        "station_name": names.get(str(a.station_id)), "kind": a.kind, "source": a.source,
        "title": a.title, "requested_by": a.requested_by, "status": a.status,
        "has_tones": a.has_tones, "audio_url": a.audio_url,
        "requested_at": a.requested_at.isoformat() if a.requested_at else None,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "ended_at": a.ended_at.isoformat() if a.ended_at else None,
        "seconds_on_air": a.seconds_on_air, "error": a.error,
    }


async def _names(db: AsyncSession) -> dict:
    return {str(i): n for i, n in (await db.execute(select(Station.id, Station.name))).all()}


async def _send(db, *, stations, file, audio_url, kind, title, source, requested_by, allow_tones):
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(KINDS)}")
    data, name, ctype = await _audio(file, audio_url)
    try:
        found = await eas_tones.find_async(data)
    except ValueError:
        raise HTTPException(status_code=422, detail="The audio could not be read") from None
    if found["has_tones"] and not allow_tones:
        raise HTTPException(status_code=422, detail=TONES_REFUSED)
    rows = await emergency_alerts.activate(
        db, station_ids=stations, data=data, filename=name, content_type=ctype, kind=kind,
        title=(title or "").strip()[:255] or None, source=source, requested_by=requested_by,
        has_tones=found["has_tones"], found=found,
    )
    await db.commit()
    names = await _names(db)
    return {"alerts": [_row(a, names) for a in rows],
            "sent": sum(1 for a in rows if a.status != "failed"), "failed": sum(1 for a in rows if a.status == "failed")}


@router.post("/device/activate", status_code=201)
async def device_activate(
    _: str = Depends(_device_token),
    file: UploadFile | None = File(default=None),
    audio_url: str | None = Form(default=None),
    station_ids: str | None = Form(default=None),
    kind: str = Form(default="alert"),
    title: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """The EAS box (or the station's own tool) puts an alert on the air now: every
    station unless `station_ids` names some. The audio plays whole above everything,
    then the programme carries on. Real EAS audio, tones included, is accepted here."""
    stations = await _stations(db, station_ids)
    return await _send(db, stations=stations, file=file, audio_url=audio_url, kind=kind, title=title,
                       source="device", requested_by="EAS box", allow_tones=True)


@router.post("/activate", status_code=201)
async def staff_activate(
    file: UploadFile | None = File(default=None),
    audio_url: str | None = Form(default=None),
    station_ids: str = Form(...),
    kind: str = Form(default="test"),
    title: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """A manager puts audio on the air through the override (a test of it, or a station
    announcement in an emergency). Stations must be named. Never EAS tones."""
    stations = await _stations(db, station_ids)
    return await _send(db, stations=stations, file=file, audio_url=audio_url, kind=kind, title=title,
                       source="staff", requested_by=user.email, allow_tones=False)


async def _stop(db: AsyncSession, alert_id: uuid.UUID, by: str) -> dict:
    alert = await db.get(EmergencyAlert, alert_id)
    if alert is None:
        # Shown on air before its row is saved (the send is still waiting for the engine):
        # Stop works then too
        if await emergency_alerts.stop_unsaved(str(alert_id), by):
            return {"ok": True, "status": "cancelled"}
        raise NotFoundError("Alert not found")
    ok = await emergency_alerts.stop(db, alert, by)
    await db.commit()
    return {"ok": ok, "status": alert.status}


@router.post("/{alert_id}/stop")
async def staff_stop(alert_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager)):
    return await _stop(db, alert_id, user.email)


@router.post("/device/{alert_id}/stop")
async def device_stop(alert_id: uuid.UUID, _: str = Depends(_device_token), db: AsyncSession = Depends(get_db)):
    return await _stop(db, alert_id, "EAS box")


@router.get("")
async def list_alerts(
    skip: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
    station_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db), _: User = Depends(require_staff),
):
    q = select(EmergencyAlert)
    count = select(func.count(EmergencyAlert.id))
    if station_id:
        q = q.where(EmergencyAlert.station_id == station_id)
        count = count.where(EmergencyAlert.station_id == station_id)
    rows = (await db.execute(q.order_by(EmergencyAlert.requested_at.desc()).offset(skip).limit(limit))).scalars().all()
    names = await _names(db)
    return {"alerts": [_row(a, names) for a in rows], "total": (await db.execute(count)).scalar() or 0}


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), _: User = Depends(require_staff)):
    """Per active station: the alert on air (if any) and how it would be reached."""
    stations = (await db.execute(select(Station).where(Station.is_active.is_(True)).order_by(Station.name))).scalars().all()
    on_air = emergency_alerts.all_active()
    out = []
    for s in stations:
        sid = str(s.id)
        out.append({
            "station_id": sid, "name": s.name,
            "player": "engine" if settings.engine_drives(sid) else "browser",
            "box": sid in settings.relay_station_ids,
            "on_air": on_air.get(sid),
        })
    return {"stations": out, "device_api": bool(settings.EMERGENCY_API_TOKEN)}


@router.get("/active")
async def active(_: User = Depends(require_staff)):
    """Only the alerts on air now (the banner every staff page shows): no station list."""
    return {"on_air": emergency_alerts.all_active()}


@router.get("/audio/{name}")
async def audio(name: str):
    """An alert's audio in local and test setups (production serves it from storage)."""
    from app.services.storage_service import LOCAL_STORAGE_DIR

    if "/" in name or ".." in name:
        raise NotFoundError("Not found")
    path = os.path.join(LOCAL_STORAGE_DIR, "emergency", name)
    if not os.path.isfile(path):
        raise NotFoundError("Not found")
    return FileResponse(path, media_type="audio/mpeg" if name.endswith(".mp3") else "audio/wav")
