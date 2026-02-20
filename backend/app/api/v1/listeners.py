"""Listener tracking endpoints — heartbeat (public) + analytics (admin)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager
from app.db.session import get_db
from app.models.listener_session import ListenerSession
from app.models.station import Station
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/listeners", tags=["listeners"])

# Sessions with no heartbeat for >90 seconds are considered inactive
ACTIVE_THRESHOLD_SECONDS = 90


class HeartbeatRequest(BaseModel):
    station_id: str
    session_key: str


def _get_client_ip(request: Request) -> str | None:
    """Extract real client IP from proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    if request.client:
        return request.client.host
    return None


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
    station_id = uuid.UUID(body.station_id)

    # Find existing session
    result = await db.execute(
        select(ListenerSession).where(
            ListenerSession.station_id == station_id,
            ListenerSession.session_key == body.session_key,
        )
    )
    session = result.scalar_one_or_none()

    if session:
        # Update existing session
        elapsed = (now - session.started_at).total_seconds()
        session.last_heartbeat = now
        session.duration_seconds = elapsed
        await db.flush()
        return {"status": "ok", "duration": elapsed}
    else:
        # Create new session with geo lookup
        ip = _get_client_ip(request)
        geo = await _resolve_geo(ip)
        ua = request.headers.get("user-agent", "")[:500]

        session = ListenerSession(
            station_id=station_id,
            session_key=body.session_key,
            ip_address=ip,
            country=geo["country"],
            region=geo["region"],
            city=geo["city"],
            started_at=now,
            last_heartbeat=now,
            duration_seconds=0,
            user_agent=ua,
        )
        db.add(session)
        await db.flush()
        return {"status": "created"}


@router.post("/disconnect")
async def disconnect(
    body: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Called when a listener stops — finalizes the session duration."""
    station_id = uuid.UUID(body.station_id)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(ListenerSession).where(
            ListenerSession.station_id == station_id,
            ListenerSession.session_key == body.session_key,
        )
    )
    session = result.scalar_one_or_none()
    if session:
        session.last_heartbeat = now
        session.duration_seconds = (now - session.started_at).total_seconds()
        await db.flush()
    return {"status": "ok"}


# ── Admin Analytics Endpoints ──────────────────────────────────────────


@router.get("/live")
async def get_live_listeners(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Current active listeners across all stations, grouped by station + region."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)

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
        .where(ListenerSession.last_heartbeat >= cutoff)
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


@router.get("/today")
async def get_today_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Today's listener stats: total sessions, unique listeners, total minutes."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = now - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)

    result = await db.execute(
        select(
            func.count(ListenerSession.id).label("total_sessions"),
            func.count(func.distinct(ListenerSession.ip_address)).label("unique_listeners"),
            func.coalesce(func.sum(ListenerSession.duration_seconds), 0).label("total_seconds"),
        )
        .where(ListenerSession.started_at >= today_start)
    )
    row = result.one()

    # Current active count
    active_result = await db.execute(
        select(func.count(ListenerSession.id))
        .where(ListenerSession.last_heartbeat >= cutoff)
    )
    active_count = active_result.scalar() or 0

    # Peak listeners today — sweep algorithm over session start/end events
    # Fetch all sessions that overlap with today, then walk events to find max concurrent
    peak = active_count
    sessions_result = await db.execute(
        select(ListenerSession.started_at, ListenerSession.last_heartbeat)
        .where(ListenerSession.last_heartbeat >= today_start)
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
