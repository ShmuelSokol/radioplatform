"""Report a bug: anyone on the site can send one; staff can read them.

See wiki/bug-reports.md and services/bug_report_service.py.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from collections import deque

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.core import log_buffer
from app.core.client_ip import client_ip
from app.core.dependencies import require_admin, require_staff
from app.core.exceptions import NotFoundError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.bug_report import BUG_STATUSES, BugReport
from app.models.user import User
from app.services import bug_report_service

router = APIRouter(prefix="/bug-reports", tags=["bug-reports"])

MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
MAX_CONTEXT_CHARS = 1_500_000
RATE_WINDOW_SECONDS = 600
RATE_MAX = 8
_recent: dict[str, deque] = {}
_background: set = set()

optional_bearer = HTTPBearer(auto_error=False)


class BugReportIn(BaseModel):
    description: str = Field(min_length=1, max_length=5000)
    expected: str | None = Field(default=None, max_length=3000)
    page_url: str | None = Field(default=None, max_length=1000)
    station_id: str | None = Field(default=None, max_length=64)
    screenshot: str | None = None  # data:image/jpeg;base64,...
    context: dict | None = None


async def _optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access" or not payload.get("sub"):
            return None
        return (await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))).scalar_one_or_none()
    except Exception:
        return None


def _rate_limited(key: str) -> bool:
    now = time.time()
    q = _recent.setdefault(key, deque())
    while q and q[0] < now - RATE_WINDOW_SECONDS:
        q.popleft()
    if len(q) >= RATE_MAX:
        return True
    q.append(now)
    return False


def _decode_screenshot(data_url: str | None) -> tuple[bytes | None, str | None]:
    if not data_url or not data_url.startswith("data:image/") or "," not in data_url:
        return None, None
    head, b64 = data_url.split(",", 1)
    mime = head[5:].split(";")[0]
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        return None, None
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        return None, None
    if not raw or len(raw) > MAX_SCREENSHOT_BYTES:  # bad base64 decodes to nothing
        return None, None
    return raw, mime


def _fit_context(ctx: dict | None) -> dict | None:
    """Keep the context under the size cap by trimming the long lists first."""
    if not ctx:
        return ctx
    for _ in range(6):
        if len(json.dumps(ctx, default=str)) <= MAX_CONTEXT_CHARS:
            return ctx
        for key in ("console", "network", "breadcrumbs", "queries"):
            if isinstance(ctx.get(key), list) and len(ctx[key]) > 20:
                ctx[key] = ctx[key][len(ctx[key]) // 2:]
            elif isinstance(ctx.get(key), dict):
                ctx[key] = {"trimmed": True}
    return {"trimmed": True, "note": "context was too large to keep"}


def _summary(r: BugReport) -> dict:
    return {
        "id": str(r.id),
        "number": r.number,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "reporter_name": r.reporter_name,
        "station_id": r.station_id,
        "description": r.description,
        "expected": r.expected,
        "page_url": r.page_url,
        "has_screenshot": bool(r.screenshot_mime),
        "github_url": r.github_url,
        "fix_url": r.fix_url,
        "mirror_error": r.mirror_error,
    }


@router.post("", status_code=201)
async def create_bug_report(
    body: BugReportIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(_optional_user),
):
    key = f"user:{user.id}" if user else f"ip:{client_ip(request) or '?'}"
    if _rate_limited(key):
        return Response(
            content=json.dumps({"detail": "Too many reports in a few minutes. Please wait a little and try again."}),
            status_code=429, media_type="application/json",
        )

    shot, mime = _decode_screenshot(body.screenshot)
    rid = getattr(request.state, "request_id", None)
    log_buffer_snapshot = log_buffer.snapshot()
    report = BugReport(
        status="new",
        reporter_id=user.id if user else None,
        reporter_name=(user.display_name or user.email) if user else None,
        station_id=body.station_id,
        description=body.description.strip(),
        expected=(body.expected or "").strip() or None,
        page_url=body.page_url,
        user_agent=(request.headers.get("user-agent") or "")[:500],
        client_context=_fit_context(body.context),
        server_log={**log_buffer_snapshot, "report_request_id": rid},
        screenshot=shot,
        screenshot_mime=mime,
    )
    # Numbers are max+1 under a unique key: a report sent at the same moment as
    # another takes the next number instead of sharing it
    for attempt in range(5):
        report.number = ((await db.execute(select(func.max(BugReport.number)))).scalar() or 0) + 1
        db.add(report)
        try:
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            if attempt == 4:
                raise
    number = report.number
    import logging
    logging.getLogger(__name__).info(
        "Bug report %s from %s on %s: %s", number, report.reporter_name or "anonymous", body.page_url, body.description[:120]
    )
    if bug_report_service.github_enabled() or bug_report_service.settings.NTFY_TOPIC:
        task = asyncio.get_running_loop().create_task(bug_report_service.report_received(report.id))
        _background.add(task)
        task.add_done_callback(_background.discard)
    return {
        "id": str(report.id), "number": number, "mirrored": bug_report_service.github_enabled(),
        # False when a screenshot was sent but not kept (too big, wrong type, unreadable)
        "screenshot_saved": bool(shot) if body.screenshot else None,
    }


@router.get("")
async def list_bug_reports(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_staff),
):
    q = select(BugReport).order_by(BugReport.number.desc()).limit(limit)
    if status:
        q = q.where(BugReport.status == status)
    rows = (await db.execute(q)).scalars().all()
    return {"reports": [_summary(r) for r in rows]}


async def _get(db: AsyncSession, report_id: str, full: bool = False) -> BugReport:
    try:
        rid = uuid.UUID(report_id)
    except ValueError:
        raise NotFoundError("Bug report not found")
    q = select(BugReport).where(BugReport.id == rid)
    if full:
        q = q.options(undefer(BugReport.server_log), undefer(BugReport.screenshot))
    r = (await db.execute(q)).scalar_one_or_none()
    if r is None:
        raise NotFoundError("Bug report not found")
    return r


@router.get("/{report_id}")
async def get_bug_report(report_id: str, db: AsyncSession = Depends(get_db), _user: User = Depends(require_staff)):
    r = await _get(db, report_id, full=True)
    return {**_summary(r), "user_agent": r.user_agent, "client_context": r.client_context, "server_log": r.server_log}


@router.get("/{report_id}/screenshot")
async def get_bug_screenshot(report_id: str, db: AsyncSession = Depends(get_db), _user: User = Depends(require_staff)):
    r = await _get(db, report_id, full=True)
    if not r.screenshot:
        raise NotFoundError("No screenshot")
    return Response(content=r.screenshot, media_type=r.screenshot_mime or "image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


class BugStatusIn(BaseModel):
    status: str


@router.patch("/{report_id}")
async def set_bug_status(report_id: str, body: BugStatusIn, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    if body.status not in BUG_STATUSES:
        return Response(content=json.dumps({"detail": "Unknown status"}), status_code=422, media_type="application/json")
    r = await _get(db, report_id)
    r.status = body.status
    await db.commit()
    return _summary(r)


@router.post("/{report_id}/resend")
async def resend_to_github(report_id: str, db: AsyncSession = Depends(get_db), _user: User = Depends(require_admin)):
    """Copy a report to GitHub again (e.g. one sent before the token was set)."""
    r = await _get(db, report_id)
    if not bug_report_service.github_enabled():
        return {"mirrored": False, "detail": "BUG_REPORTS_GITHUB_TOKEN is not set"}
    await bug_report_service.mirror_to_github(r.id)
    await db.refresh(r)
    return _summary(r)
