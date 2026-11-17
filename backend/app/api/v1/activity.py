"""Activity page: who did what on the site, and who listened (wiki/activity-log.md)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_admin
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.listener_session import ListenerSession
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import activity_log

router = APIRouter(prefix="/activity", tags=["activity"])


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


async def _station_names(db: AsyncSession) -> dict[str, str]:
    res = await db.execute(select(Station.id, Station.name))
    return {str(i): n for i, n in res.all()}


def _row(log: AuditLog, stations: dict[str, str]) -> dict:
    sid = str(log.station_id) if log.station_id else None
    return {
        "id": str(log.id),
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "user_id": str(log.user_id) if log.user_id else None,
        "user_email": log.user_email,
        "user_name": log.user_name,
        "user_role": log.user_role,
        "label": log.label or f"{(log.action or '').capitalize()} {log.resource_type}",
        "category": log.category or "other",
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "detail": log.detail,
        "changes": log.changes,
        "station_id": sid,
        "station_name": stations.get(sid) if sid else None,
        "method": log.method,
        "path": log.path,
        "status_code": log.status_code,
        "ip_address": log.ip_address,
        "device": log.device,
        "user_agent": log.user_agent,
        "location": log.location,
        "request_id": log.request_id,
    }


def _conditions(
    *, user_id: str | None, user_email: str | None, station_id: str | None, category: str | None,
    q: str | None, start: str | None, end: str | None, include_views: bool, failed_only: bool,
    after: str | None = None,
):
    conds = []
    uid = _uuid(user_id)
    if user_id and uid:
        conds.append(AuditLog.user_id == uid)
    if user_email:
        conds.append(AuditLog.user_id.is_(None))
        conds.append(func.lower(AuditLog.user_email) == user_email.lower())
    sid = _uuid(station_id)
    if station_id and sid:
        conds.append(AuditLog.station_id == sid)
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if cats:
            if "other" in cats:
                conds.append(or_(AuditLog.category.in_(cats), AuditLog.category.is_(None)))
            else:
                conds.append(AuditLog.category.in_(cats))
    if not include_views:
        conds.append(or_(AuditLog.category.is_(None), AuditLog.category != "view"))
    if failed_only:
        conds.append(AuditLog.status_code >= 400)
    t0, t1, ta = _parse_time(start), _parse_time(end), _parse_time(after)
    if t0:
        conds.append(AuditLog.created_at >= t0)
    if t1:
        conds.append(AuditLog.created_at < t1)
    if ta:
        conds.append(AuditLog.created_at > ta)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        conds.append(or_(
            func.lower(func.coalesce(AuditLog.label, "")).like(like),
            func.lower(func.coalesce(AuditLog.detail, "")).like(like),
            func.lower(func.coalesce(AuditLog.user_email, "")).like(like),
            func.lower(func.coalesce(AuditLog.user_name, "")).like(like),
            func.lower(func.coalesce(AuditLog.path, "")).like(like),
            func.lower(func.coalesce(AuditLog.ip_address, "")).like(like),
            func.lower(func.coalesce(AuditLog.location, "")).like(like),
        ))
    return and_(*conds) if conds else True


@router.get("")
async def list_activity(
    user_id: str | None = Query(None),
    user_email: str | None = Query(None, description="rows with no account (e.g. failed sign-ins) for this email"),
    station_id: str | None = Query(None),
    category: str | None = Query(None, description="comma-separated"),
    q: str | None = Query(None, max_length=200),
    start: str | None = Query(None, description="ISO time, inclusive"),
    end: str | None = Query(None, description="ISO time, exclusive"),
    include_views: bool = Query(True),
    failed_only: bool = Query(False),
    after: str | None = Query(None, description="only rows newer than this ISO time (live updates)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    where = _conditions(
        user_id=user_id, user_email=user_email, station_id=station_id, category=category, q=q,
        start=start, end=end, include_views=include_views, failed_only=failed_only, after=after,
    )
    stations = await _station_names(db)
    rows = (await db.execute(
        select(AuditLog).where(where).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    )).scalars().all()
    total = None
    if not after:  # live polls only need the new rows
        total = (await db.execute(select(func.count()).select_from(AuditLog).where(where))).scalar() or 0
    return {"total": total, "items": [_row(r, stations) for r in rows]}


@router.get("/people")
async def activity_by_person(
    station_id: str | None = Query(None),
    category: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    start: str | None = Query(None),
    end: str | None = Query(None),
    include_views: bool = Query(True),
    failed_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """One line per person for the period: how many actions, screens opened,
    refused or failed, by kind, and when they last did something."""
    where = _conditions(
        user_id=None, user_email=None, station_id=station_id, category=category, q=q,
        start=start, end=end, include_views=include_views, failed_only=failed_only,
    )
    failed = func.sum(case((AuditLog.status_code >= 400, literal_column("1")), else_=literal_column("0")))
    # No bound parameters in the grouped expression: Postgres must see the same text
    # in SELECT and GROUP BY (with "" as a parameter it saw $1 and $2 and refused)
    email = func.lower(func.coalesce(AuditLog.user_email, literal_column("''")))
    res = await db.execute(
        select(
            AuditLog.user_id, email, AuditLog.category,
            func.count(), failed, func.max(AuditLog.created_at),
        ).where(where).group_by(AuditLog.user_id, email, AuditLog.category)
    )
    people: dict[str, dict] = {}
    for uid, email, cat, n, n_failed, last in res.all():
        key = f"u:{uid}" if uid else f"e:{email}"
        p = people.setdefault(key, {
            "key": key, "user_id": str(uid) if uid else None, "user_email": email or None,
            "actions": 0, "views": 0, "failed": 0, "last_at": None, "by_category": {},
        })
        cat = cat or "other"
        if cat == "view":
            p["views"] += n
        else:
            p["actions"] += n
        p["failed"] += int(n_failed or 0)
        p["by_category"][cat] = p["by_category"].get(cat, 0) + n
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and (p["last_at"] is None or last > p["last_at"]):
            p["last_at"] = last
        if uid and email and not p["user_email"]:
            p["user_email"] = email

    ids = [uuid.UUID(p["user_id"]) for p in people.values() if p["user_id"]]
    info: dict[str, tuple] = {}
    if ids:
        for i, e, n, r in (await db.execute(
            select(User.id, User.email, User.display_name, User.role).where(User.id.in_(ids))
        )).all():
            info[str(i)] = (e, n, r.value if hasattr(r, "value") else r)
    out = []
    for p in people.values():
        if p["user_id"] and p["user_id"] in info:
            e, n, r = info[p["user_id"]]
            p.update(user_email=e, user_name=n, user_role=r)
        else:
            p.update(user_name=None, user_role=None)
        p["last_at"] = p["last_at"].isoformat() if p["last_at"] else None
        out.append(p)
    out.sort(key=lambda p: (p["actions"] + p["views"], p["last_at"] or ""), reverse=True)
    return {"people": out}


@router.get("/filters")
async def activity_filters(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """People and stations to filter by, and the action groups."""
    users = (await db.execute(
        select(User.id, User.email, User.display_name, User.role, User.last_seen_at)
        .where(User.role != UserRole.SPONSOR).order_by(User.email)
    )).all()
    stations = (await db.execute(select(Station.id, Station.name).order_by(Station.name))).all()
    return {
        "users": [
            {"id": str(i), "email": e, "name": n, "role": r.value if hasattr(r, "value") else r,
             "last_seen_at": s.isoformat() if s else None}
            for i, e, n, r, s in users
        ],
        "stations": [{"id": str(i), "name": n} for i, n in stations],
        "categories": [{"id": k, "label": v} for k, v in activity_log.CATEGORIES.items()],
    }


class PageView(BaseModel):
    path: str = Field(max_length=300)
    title: str | None = Field(None, max_length=200)
    station_id: str | None = None


@router.post("/page-view", status_code=204)
async def page_view(
    body: PageView,
    request: Request,
    user: User = Depends(get_current_user),
):
    """The admin site reports the screens a signed-in user opens."""
    if user.role == UserRole.SPONSOR and not body.path.startswith("/sponsor"):
        return None
    activity_log.record(
        request,
        user_id=str(user.id),
        user_email=user.email,
        user_role=user.role.value,
        label=f"Opened {body.title or body.path}"[:200],
        category="view",
        action="view",
        resource_type="page",
        resource_id=body.path[:100],
        detail=body.path,
        station_id=str(_uuid(body.station_id)) if _uuid(body.station_id) else None,
        status_code=200,
    )
    return None


@router.get("/listeners")
async def listener_sessions(
    station_id: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    start: str | None = Query(None),
    end: str | None = Query(None),
    min_seconds: int = Query(0, ge=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Each listening session: station, when, how long, where from, which device."""
    conds = []
    sid = _uuid(station_id)
    if station_id and sid:
        conds.append(ListenerSession.station_id == sid)
    t0, t1 = _parse_time(start), _parse_time(end)
    if t0:
        conds.append(ListenerSession.started_at >= t0)
    if t1:
        conds.append(ListenerSession.started_at < t1)
    if min_seconds:
        conds.append(ListenerSession.duration_seconds >= min_seconds)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        conds.append(or_(
            func.lower(func.coalesce(ListenerSession.city, "")).like(like),
            func.lower(func.coalesce(ListenerSession.region, "")).like(like),
            func.lower(func.coalesce(ListenerSession.country, "")).like(like),
            func.lower(func.coalesce(ListenerSession.ip_address, "")).like(like),
            func.lower(func.coalesce(ListenerSession.user_agent, "")).like(like),
        ))
    where = and_(*conds) if conds else True
    stations = await _station_names(db)
    total = (await db.execute(select(func.count()).select_from(ListenerSession).where(where))).scalar() or 0
    rows = (await db.execute(
        select(ListenerSession).where(where).order_by(ListenerSession.started_at.desc()).offset(skip).limit(limit)
    )).scalars().all()
    from app.api.v1.listeners import ACTIVE_THRESHOLD_SECONDS
    active_cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACTIVE_THRESHOLD_SECONDS)

    def aware(t: datetime | None) -> datetime | None:
        return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t

    items = []
    for s in rows:
        hb = aware(s.last_heartbeat)
        loc = ", ".join(dict.fromkeys(p for p in (s.city, s.region, s.country) if p and p != "Unknown")) or None
        items.append({
            "id": str(s.id),
            "station_id": str(s.station_id),
            "station_name": stations.get(str(s.station_id)),
            "started_at": aware(s.started_at).isoformat() if s.started_at else None,
            "last_heartbeat": hb.isoformat() if hb else None,
            "ended_at": aware(s.ended_at).isoformat() if s.ended_at else None,
            "listening_now": s.ended_at is None and hb is not None and hb >= active_cutoff,
            "duration_seconds": round(s.duration_seconds or 0),
            "location": loc,
            "ip_address": s.ip_address,
            "device": activity_log.describe_device(s.user_agent),
            "user_agent": s.user_agent,
        })
    return {"total": total, "items": items}
