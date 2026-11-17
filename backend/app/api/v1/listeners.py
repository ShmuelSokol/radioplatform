"""Listener tracking endpoints — heartbeat (public) + analytics (admin)."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import client_ip
from app.core.dependencies import require_manager
from app.db.engine import async_session_factory
from app.db.session import get_db
from app.models.listener_session import ListenerSession
from app.models.station import Station
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/listeners", tags=["listeners"])

# Sessions with no heartbeat for >90 seconds are considered inactive
ACTIVE_THRESHOLD_SECONDS = 90


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite returns naive times


class HeartbeatRequest(BaseModel):
    # A malformed station id is the caller's mistake: 422, not a 500 from uuid.UUID()
    station_id: uuid.UUID
    # The column is String(100); a retired session gets "~<8 chars>" appended (_retire)
    session_key: str = Field(min_length=1, max_length=80)


def _retire(session: ListenerSession) -> None:
    """Free the session key of a session that ended (Stop), so the next Listen with
    the same key starts a new session instead of reopening this one. Reopening it
    counted the stopped gap as listening time (duration = now - started_at) and made
    the session overlap the gap in the peak count."""
    session.session_key = f"{session.session_key}~{str(session.id).replace('-', '')[:8]}"


def _for_key(station_id: uuid.UUID, session_key: str):
    """The sessions with this key, newest first. Two first heartbeats arriving at
    the same moment can each create a row (no unique constraint on old databases);
    the newest is used and the rest are left alone, instead of every later heartbeat
    failing with MultipleResultsFound."""
    return (
        select(ListenerSession)
        .where(ListenerSession.station_id == station_id, ListenerSession.session_key == session_key)
        .order_by(ListenerSession.started_at.desc(), ListenerSession.last_heartbeat.desc())
    )


# ── Live push: admin pages hear "listeners changed" the moment someone joins or
# leaves, then fetch the count (the socket itself carries no listener data).
_watchers: set[WebSocket] = set()
WATCH_PING_SECONDS = 25


async def _notify_watchers() -> None:
    async def send(ws: WebSocket) -> None:
        try:
            await asyncio.wait_for(ws.send_text('{"type":"listeners"}'), timeout=2)
        except Exception:
            _watchers.discard(ws)

    await asyncio.gather(*(send(ws) for ws in list(_watchers)))


def _live():
    """Sessions that count as listening now."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)
    return (ListenerSession.last_heartbeat >= cutoff) & ListenerSession.ended_at.is_(None)


def _get_client_ip(request: Request) -> str | None:
    """The listener's address as Railway's proxy saw it (see app/core/client_ip.py)."""
    return client_ip(request)


# A new listener's heartbeat waits this long for the location, then answers and
# fills the location in when the lookup finishes (ip-api.com can take 3 s).
GEO_WAIT_SECONDS = 0.1
_geo_tasks: set[asyncio.Task] = set()


async def _fill_geo_later(session_id: uuid.UUID, lookup: asyncio.Future) -> None:
    try:
        geo = await lookup
        async with async_session_factory() as db:
            row = await db.get(ListenerSession, session_id)
            if row is not None:
                row.country, row.region, row.city = geo["country"], geo["region"], geo["city"]
                await db.commit()
    except Exception as exc:
        logger.debug("Late GeoIP fill failed for %s: %s", session_id, exc)


async def _resolve_geo(ip: str) -> dict:
    """Look up geographic info for an IP address via ip-api.com (free, no key)."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return {"country": "Local", "region": "Local", "city": "Local"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=country,regionName,city")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("country"):
                    return {
                        "country": data.get("country", "Unknown"),
                        "region": data.get("regionName", ""),
                        "city": data.get("city", ""),
                    }
    except Exception as exc:
        logger.debug("GeoIP lookup failed for %s: %s", ip, exc)
    return {"country": "Unknown", "region": "", "city": ""}


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — called by the Listen page every 30s to track active listeners."""
    now = datetime.now(timezone.utc)
    station_id = body.station_id

    sessions = (await db.execute(_for_key(station_id, body.session_key))).scalars().all()
    session = sessions[0] if sessions else None
    if session is not None and session.ended_at is not None:
        # Stop, then Listen again on the same page (same key): a new session
        for old in sessions:
            _retire(old)
        await db.flush()
        session = None

    if session:
        # Update existing session
        elapsed = (now - _aware(session.started_at)).total_seconds()
        rejoined = (
            session.ended_at is not None
            or (now - _aware(session.last_heartbeat)).total_seconds() > ACTIVE_THRESHOLD_SECONDS
        )
        session.last_heartbeat = now
        session.duration_seconds = elapsed
        session.ended_at = None
        await db.commit()
        if rejoined:
            await _notify_watchers()
        return {"status": "ok", "duration": elapsed}
    else:
        if await db.get(Station, station_id) is None:
            raise HTTPException(404, "Station not found")
        # Create new session with geo lookup
        ip = _get_client_ip(request)
        lookup = asyncio.ensure_future(_resolve_geo(ip))
        try:
            geo = await asyncio.wait_for(asyncio.shield(lookup), timeout=GEO_WAIT_SECONDS)
        except asyncio.TimeoutError:
            geo = None
        ua = request.headers.get("user-agent", "")[:500]

        session = ListenerSession(
            station_id=station_id,
            session_key=body.session_key,
            ip_address=ip,
            country=geo["country"] if geo else None,
            region=geo["region"] if geo else None,
            city=geo["city"] if geo else None,
            started_at=now,
            last_heartbeat=now,
            duration_seconds=0,
            user_agent=ua,
        )
        db.add(session)
        await db.commit()
        if geo is None:
            task = asyncio.create_task(_fill_geo_later(session.id, lookup))
            _geo_tasks.add(task)
            task.add_done_callback(_geo_tasks.discard)
        await _notify_watchers()
        return {"status": "created"}


async def _end_session(db: AsyncSession, station_id: uuid.UUID, session_key: str) -> None:
    now = datetime.now(timezone.utc)
    sessions = (await db.execute(_for_key(station_id, session_key))).scalars().all()
    open_ones = [s for s in sessions if s.ended_at is None]
    for session in open_ones:
        session.last_heartbeat = now
        session.duration_seconds = (now - _aware(session.started_at)).total_seconds()
        session.ended_at = now
    if open_ones:
        await db.commit()
        await _notify_watchers()


@router.post("/disconnect")
async def disconnect(
    body: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Called when a listener stops — finalizes the session and drops it from the live count."""
    await _end_session(db, body.station_id, body.session_key)
    return {"status": "ok"}


@router.post("/leave")
async def leave(request: Request, db: AsyncSession = Depends(get_db)):
    """Same as /disconnect, for navigator.sendBeacon when the page closes.

    A beacon cannot send application/json across origins without a preflight it
    never makes, so it sends the same body as text/plain.
    """
    try:
        body = HeartbeatRequest(**json.loads(await request.body()))
        station_id = body.station_id
    except Exception:
        return {"status": "ignored"}
    await _end_session(db, station_id, body.session_key)
    return {"status": "ok"}


@router.websocket("/ws")
async def watch_listeners(websocket: WebSocket):
    """Admin pages keep this open and refetch /live when told something changed."""
    await websocket.accept()
    _watchers.add(websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=WATCH_PING_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text('{"type":"ping"}')  # keeps proxies from closing it
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _watchers.discard(websocket)


# ── Admin Analytics Endpoints ──────────────────────────────────────────


@router.get("/live")
async def get_live_listeners(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Current active listeners across all stations, grouped by station + region."""

    # Active sessions (heartbeat within threshold)
    result = await db.execute(
        select(
            ListenerSession.station_id,
            Station.name.label("station_name"),
            ListenerSession.country,
            ListenerSession.region,
            ListenerSession.city,
            func.count(ListenerSession.id).label("count"),
        )
        .join(Station, ListenerSession.station_id == Station.id)
        .where(_live())
        .group_by(
            ListenerSession.station_id,
            Station.name,
            ListenerSession.country,
            ListenerSession.region,
            ListenerSession.city,
        )
        .order_by(Station.name, func.count(ListenerSession.id).desc())
    )
    rows = result.all()

    # Aggregate by station
    stations: dict[str, dict] = {}
    total_listeners = 0
    for row in rows:
        sid = str(row.station_id)
        if sid not in stations:
            stations[sid] = {
                "station_id": sid,
                "station_name": row.station_name,
                "listeners": 0,
                "regions": [],
            }
        stations[sid]["listeners"] += row.count
        total_listeners += row.count
        stations[sid]["regions"].append({
            "country": row.country or "Unknown",
            "region": row.region or "",
            "city": row.city or "",
            "count": row.count,
        })

    return {
        "total_listeners": total_listeners,
        "stations": list(stations.values()),
    }


def _local_midnight(now: datetime, tz_name: str | None) -> datetime:
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = timezone.utc
    return now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


@router.get("/today")
async def get_today_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Today's listener stats: total sessions, unique listeners, total minutes."""
    now = datetime.now(timezone.utc)
    # "Today" begins at each station's own local midnight, not at UTC midnight
    # (8 pm the evening before in New York in summer)
    day_starts = {
        sid: _local_midnight(now, tz) for sid, tz in (await db.execute(select(Station.id, Station.timezone))).all()
    }

    def since_local_midnight(col):
        if not day_starts:
            return col >= now.replace(hour=0, minute=0, second=0, microsecond=0)
        return or_(*((ListenerSession.station_id == sid) & (col >= start) for sid, start in day_starts.items()))

    result = await db.execute(
        select(
            func.count(ListenerSession.id).label("total_sessions"),
            func.count(func.distinct(ListenerSession.ip_address)).label("unique_listeners"),
            func.coalesce(func.sum(ListenerSession.duration_seconds), 0).label("total_seconds"),
        )
        .where(since_local_midnight(ListenerSession.started_at))
    )
    row = result.one()

    # Current active count
    active_result = await db.execute(
        select(func.count(ListenerSession.id))
        .where(_live())
    )
    active_count = active_result.scalar() or 0

    # Peak listeners today — sweep algorithm over session start/end events
    # Fetch all sessions that overlap with today, then walk events to find max concurrent
    peak = active_count
    sessions_result = await db.execute(
        select(ListenerSession.started_at, ListenerSession.last_heartbeat)
        .where(since_local_midnight(ListenerSession.last_heartbeat))
    )
    events: list[tuple[datetime, int]] = []
    for s in sessions_result.all():
        events.append((s.started_at, 1))   # session starts
        events.append((s.last_heartbeat, -1))  # session ends
    if events:
        events.sort(key=lambda e: (e[0], e[1]))  # sort by time, ends before starts at same time
        concurrent = 0
        for _, delta in events:
            concurrent += delta
            if concurrent > peak:
                peak = concurrent

    return {
        "date": now.strftime("%Y-%m-%d"),
        "total_sessions": row.total_sessions,
        "unique_listeners": row.unique_listeners,
        "total_minutes": round(row.total_seconds / 60, 1),
        "active_now": active_count,
        "peak_today": peak,
    }


@router.get("/history")
async def get_listener_history(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Daily listener aggregates for charting."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            cast(ListenerSession.started_at, Date).label("date"),
            func.count(ListenerSession.id).label("sessions"),
            func.count(func.distinct(ListenerSession.ip_address)).label("unique_listeners"),
            func.coalesce(func.sum(ListenerSession.duration_seconds), 0).label("total_seconds"),
        )
        .where(ListenerSession.started_at >= cutoff)
        .group_by(cast(ListenerSession.started_at, Date))
        .order_by(cast(ListenerSession.started_at, Date))
    )
    rows = result.all()

    return {
        "days": [
            {
                "date": str(row.date),
                "sessions": row.sessions,
                "unique_listeners": row.unique_listeners,
                "total_minutes": round(row.total_seconds / 60, 1),
            }
            for row in rows
        ]
    }


@router.get("/regions")
async def get_listener_regions(
    days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Listener breakdown by country/region over a period."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            ListenerSession.country,
            ListenerSession.region,
            ListenerSession.city,
            func.count(ListenerSession.id).label("sessions"),
            func.count(func.distinct(ListenerSession.ip_address)).label("unique_listeners"),
            func.coalesce(func.sum(ListenerSession.duration_seconds), 0).label("total_seconds"),
        )
        .where(ListenerSession.started_at >= cutoff)
        .group_by(ListenerSession.country, ListenerSession.region, ListenerSession.city)
        .order_by(func.count(ListenerSession.id).desc())
    )
    rows = result.all()

    return {
        "regions": [
            {
                "country": row.country or "Unknown",
                "region": row.region or "",
                "city": row.city or "",
                "sessions": row.sessions,
                "unique_listeners": row.unique_listeners,
                "total_minutes": round(row.total_seconds / 60, 1),
            }
            for row in rows
        ]
    }
