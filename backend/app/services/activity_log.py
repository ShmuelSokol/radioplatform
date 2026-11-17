"""Activity log: who did what on the site (see wiki/activity-log.md).

One row per change a signed-in user makes, written without slowing the request:

* ``ActivityMiddleware`` notices every POST/PUT/PATCH/DELETE that carries a
  valid sign-in, names it ("Pressed Next", "Changed playlist"), and queues a row.
* A SQLAlchemy ``before_flush`` hook collects what the request changed in the
  database (field by field, old and new), so rows show before/after for every
  screen without each route having to log it.
* Sign-ins (and failed ones) and admin pages opened are recorded explicitly.
* Rows go through an in-memory queue to one background writer that inserts
  them in batches, so the request never waits for the log.
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import decimal
import enum
import json
import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy import event, inspect as sa_inspect, select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Calls that change nothing worth showing, or are not made by a person.
EXCLUDED_PREFIXES = (
    "/listeners/",            # listener heartbeats
    "/auth/refresh",
    "/auth/login",            # recorded by the login route itself
    "/users/me/preferences",
    "/playout/",              # playout engine, not a person
    "/activity/",             # page views are recorded by their own route
    "/billing/webhook",
    "/live-shows/twilio/",
    "/crm/",                  # listener club members, not staff
    "/call-in/",
)
EXCLUDED_EXACT = {
    ("POST", "/sponsorship/copy-review"),
    ("POST", "/holidays/preview"),
    ("POST", "/stations/{station_id}/queue/preview-weather"),
    ("POST", "/assets/{asset_id}/enhance-preview"),
    ("POST", "/ai-emails/draft"),
}

# Named actions. Key: (method, route template after /api/v1). Value: (label, category).
LABELS: dict[tuple[str, str], tuple[str, str]] = {
    # Control room
    ("POST", "/stations/{station_id}/queue/skip"): ("Pressed Next", "control"),
    ("POST", "/stations/{station_id}/queue/fire-now"): ("Played now", "control"),
    ("POST", "/stations/{station_id}/queue/insert"): ("Inserted into the queue", "control"),
    ("POST", "/stations/{station_id}/queue/add"): ("Added to the queue", "control"),
    ("POST", "/stations/{station_id}/queue/bulk-add"): ("Added items to the queue", "control"),
    ("POST", "/stations/{station_id}/queue/play-next"): ("Set to play next", "control"),
    ("POST", "/stations/{station_id}/queue/loop"): ("Set loop", "control"),
    ("POST", "/stations/{station_id}/queue/mix-over"): ("Mixed over", "control"),
    ("POST", "/stations/{station_id}/queue/move-up"): ("Moved up in the queue", "control"),
    ("POST", "/stations/{station_id}/queue/move-down"): ("Moved down in the queue", "control"),
    ("PUT", "/stations/{station_id}/queue/reorder"): ("Reordered the queue", "control"),
    ("POST", "/stations/{station_id}/queue/reorder-dnd"): ("Reordered the queue", "control"),
    ("POST", "/stations/{station_id}/queue/force-replenish"): ("Refilled the queue", "control"),
    ("POST", "/stations/{station_id}/queue/start"): ("Started playback", "control"),
    ("POST", "/stations/{station_id}/queue/insert-weather"): ("Inserted the weather", "control"),
    ("POST", "/stations/{station_id}/queue/schedule-hourly"): ("Scheduled the hourly items", "control"),
    ("DELETE", "/stations/{station_id}/queue/{entry_id}"): ("Removed from the queue", "control"),
    ("POST", "/stations/{station_id}/controls/play"): ("Pressed Play", "control"),
    ("POST", "/stations/{station_id}/controls/pause"): ("Pressed Pause", "control"),
    ("POST", "/stations/{station_id}/controls/stop"): ("Pressed Stop", "control"),
    ("POST", "/stations/{station_id}/controls/play-now"): ("Played now", "control"),
    ("POST", "/stations/{station_id}/controls/enqueue"): ("Added to the queue", "control"),
    ("PATCH", "/now-playing/{station_id}"): ("Changed what is on air", "control"),
    ("DELETE", "/now-playing/{station_id}"): ("Cleared what is on air", "control"),
    ("POST", "/scheduler/start"): ("Started the scheduler", "control"),
    ("POST", "/scheduler/stop"): ("Stopped the scheduler", "control"),
    # Live shows and the call screener
    ("POST", "/live-shows"): ("Created a live show", "live"),
    ("PATCH", "/live-shows/{show_id}"): ("Changed a live show", "live"),
    ("DELETE", "/live-shows/{show_id}"): ("Deleted a live show", "live"),
    ("POST", "/live-shows/{show_id}/start"): ("Went live", "live"),
    ("POST", "/live-shows/{show_id}/end"): ("Ended the live show", "live"),
    ("POST", "/live-shows/{show_id}/calls/{call_id}/approve"): ("Approved a caller", "live"),
    ("POST", "/live-shows/{show_id}/calls/{call_id}/reject"): ("Rejected a caller", "live"),
    ("POST", "/live-shows/{show_id}/calls/{call_id}/on-air"): ("Put a caller on air", "live"),
    ("POST", "/live-shows/{show_id}/calls/{call_id}/end-call"): ("Ended a call", "live"),
    ("PATCH", "/live-shows/{show_id}/calls/{call_id}"): ("Updated a caller", "live"),
    ("POST", "/icecast/{station_id}/start"): ("Started the FM feed", "live"),
    ("POST", "/icecast/{station_id}/stop"): ("Stopped the FM feed", "live"),
    # Users and permissions
    ("POST", "/users"): ("Added a user", "users"),
    ("PUT", "/users/{user_id}"): ("Changed a user", "users"),
    ("DELETE", "/users/{user_id}"): ("Deleted a user", "users"),
    # Site
    ("POST", "/site-release/push"): ("Pushed a site update", "settings"),
    ("DELETE", "/site-release/push"): ("Cancelled a site update push", "settings"),
    ("PUT", "/site-release"): ("Changed how site updates arrive", "settings"),
    ("POST", "/alerts/resolve-all"): ("Resolved all alerts", "settings"),
    ("POST", "/assets/upload"): ("Uploaded audio", "library"),
    ("POST", "/schedules/look-ahead/settle"): ("Settled a schedule conflict", "schedule"),
    ("PUT", "/sponsorship/rules"): ("Changed the sponsorship rules", "sponsors"),
    ("POST", "/spot-recordings/{recording_id}/approve"): ("Approved a spot", "sponsors"),
    ("POST", "/spot-recordings/{recording_id}/reject"): ("Rejected a spot", "sponsors"),
    ("POST", "/playlists/generation/regenerate"): ("Rebuilt the playlists", "schedule"),
    ("PUT", "/playlists/generation"): ("Changed playlist generation", "schedule"),
}

# First path segment -> (noun, category), for calls without a named action.
SEGMENTS: dict[str, tuple[str, str]] = {
    "stations": ("station", "settings"),
    "assets": ("library item", "library"),
    "categories": ("category", "library"),
    "asset-types": ("item type", "library"),
    "reviews": ("review", "library"),
    "weather-readouts": ("weather readout", "library"),
    "weather-spots": ("weather spot", "library"),
    "time-announcements": ("time announcement", "library"),
    "studio": ("studio mix", "library"),
    "schedules": ("schedule", "schedule"),
    "playlists": ("playlist", "schedule"),
    "rules": ("rule", "schedule"),
    "holidays": ("blackout", "schedule"),
    "program-blocks": ("program block", "schedule"),
    "spot-schedules": ("spot schedule", "sponsors"),
    "sponsors": ("sponsor", "sponsors"),
    "spot-recordings": ("spot recording", "sponsors"),
    "sponsorship": ("sponsorship rules", "sponsors"),
    "campaigns": ("campaign", "sponsors"),
    "billing": ("invoice", "sponsors"),
    "ai-emails": ("email", "sponsors"),
    "live-shows": ("live show", "live"),
    "users": ("user", "users"),
    "site-release": ("site update", "settings"),
    "channels": ("channel", "settings"),
    "alerts": ("alert", "settings"),
    "bug-reports": ("bug report", "settings"),
    "archives": ("archive", "settings"),
    "song-requests": ("song request", "settings"),
    "auth": ("account", "settings"),
}
# Later literal segments that name the thing better than the first one.
SUB_NOUNS = {
    "blocks": "schedule block",
    "playlist-entries": "playlist entry",
    "slots": "playlist slot",
    "drafts": "draft",
    "comments": "comment",
    "queues": "review queue",
    "items": "review item",
    "invoices": "invoice",
    "timing-defaults": "spot timing",
}

CATEGORIES = {
    "signin": "Sign-ins",
    "control": "Control room",
    "live": "Live shows",
    "library": "Library",
    "schedule": "Scheduling",
    "sponsors": "Sponsors",
    "users": "Users",
    "settings": "Settings",
    "view": "Pages opened",
    "other": "Other",
}

# Columns never shown: bookkeeping, or secrets.
SKIP_FIELDS = {"created_at", "updated_at", "last_seen_at", "last_action"}
SECRET_FIELD = re.compile(r"password|secret|token|api_key", re.I)
NAME_FIELDS = ("title", "name", "display_name", "email", "label", "caller_name")
MAX_OBJECTS = 15
MAX_VALUE = 300


# ---------------------------------------------------------------------------
# What the request changed in the database


class _Capture:
    __slots__ = ("objects", "counts")

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.counts: dict[tuple[str, str], int] = {}


_capture: contextvars.ContextVar[_Capture | None] = contextvars.ContextVar("activity_capture", default=None)


def _plain(value: Any) -> Any:
    """A JSON-safe, short form of a column value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (uuid.UUID, decimal.Decimal)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, default=str)
        except Exception:
            text = str(value)
        if len(text) <= MAX_VALUE:
            return json.loads(text) if not isinstance(value, tuple) else list(value)
        return text[:MAX_VALUE] + "…"
    text = str(value)
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def _name_of(obj: Any) -> str | None:
    for f in NAME_FIELDS:
        v = getattr(obj, f, None) if f in getattr(obj, "__dict__", {}) else None
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    return None


def _record(op: str, obj: Any, cap: _Capture) -> None:
    from app.models.audit_log import AuditLog
    from app.models.user import User

    if isinstance(obj, AuditLog):
        return
    state = sa_inspect(obj)
    table = getattr(obj, "__tablename__", type(obj).__name__)
    oid = state.identity[0] if state.identity else getattr(obj, "id", None)
    key = (table, str(oid) if oid is not None else f"new-{id(obj)}")
    fields: dict[str, dict] = {}
    if op == "update":
        for attr in state.mapper.column_attrs:
            name = attr.key
            if name in SKIP_FIELDS:
                continue
            hist = state.attrs[name].history
            if not hist.has_changes():
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else None
            if old == new:
                continue
            if SECRET_FIELD.search(name):
                fields[name] = {"old": "•••", "new": "(changed)"}
            else:
                fields[name] = {"old": _plain(old), "new": _plain(new)}
        if not fields:
            return  # e.g. only last_seen_at moved
        if isinstance(obj, User) and set(fields) <= SKIP_FIELDS:
            return
    elif op == "create":
        for attr in state.mapper.column_attrs:
            name = attr.key
            if name in SKIP_FIELDS or name == "id" or SECRET_FIELD.search(name):
                continue
            v = state.dict.get(name)
            if v is not None:
                fields[name] = {"old": None, "new": _plain(v)}

    count_key = (table, op)
    cap.counts[count_key] = cap.counts.get(count_key, 0) + 1
    existing = cap.objects.get(key)
    if existing is not None:
        for name, change in fields.items():
            if name in existing["fields"]:
                existing["fields"][name]["new"] = change["new"]
            else:
                existing["fields"][name] = change
        if op == "delete":
            existing["op"] = "delete"
        return
    if len(cap.objects) >= MAX_OBJECTS:
        return
    cap.objects[key] = {
        "type": table,
        "id": None if key[1].startswith("new-") else key[1],
        "name": _name_of(obj),
        "op": op,
        "fields": fields,
        "station_id": _plain(getattr(obj, "station_id", None)) if hasattr(obj, "station_id") else None,
        "asset_id": _plain(getattr(obj, "asset_id", None)) if hasattr(obj, "asset_id") else None,
        "_obj": obj,
    }


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context, instances) -> None:  # noqa: ARG001
    cap = _capture.get()
    if cap is None:
        return
    try:
        for obj in list(session.new):
            _record("create", obj, cap)
        for obj in list(session.dirty):
            if session.is_modified(obj, include_collections=False):
                _record("update", obj, cap)
        for obj in list(session.deleted):
            _record("delete", obj, cap)
    except Exception as e:  # never break a save over the log
        logger.debug("activity capture failed: %s", e)


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, flush_context) -> None:  # noqa: ARG001
    """New rows get their ids at flush; fill them in."""
    cap = _capture.get()
    if cap is None:
        return
    for item in cap.objects.values():
        obj = item.get("_obj")
        if item["id"] is None and obj is not None:
            oid = getattr(obj, "id", None)
            if oid is not None:
                item["id"] = str(oid)
        if item["name"] is None and obj is not None:
            try:
                item["name"] = _name_of(obj)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Naming a request


def describe(method: str, template: str) -> tuple[str, str]:
    """(label, category) for a route template such as /assets/{asset_id}/trim."""
    named = LABELS.get((method, template))
    if named:
        return named
    parts = [p for p in template.strip("/").split("/") if p]
    literals = [p for p in parts if not p.startswith("{")]
    first = literals[0] if literals else ""
    noun, category = SEGMENTS.get(first, (first.replace("-", " ") or "item", "other"))
    if first == "stations" and ("queue" in literals or "controls" in literals):
        category = "control"
    for lit in literals[1:]:
        if lit in SUB_NOUNS:
            noun = SUB_NOUNS[lit]
    last = parts[-1] if parts else ""
    is_action = bool(last) and not last.startswith("{") and last not in SUB_NOUNS and last != first
    if is_action:
        words = last.replace("-", " ")
        return f"{words[:1].upper()}{words[1:]} ({noun})", category
    verb = {"POST": "Added", "PUT": "Changed", "PATCH": "Changed", "DELETE": "Deleted"}.get(method, method)
    return f"{verb} {noun}", category


def action_kind(method: str, template: str) -> str:
    last = template.rstrip("/").rsplit("/", 1)[-1]
    if method == "DELETE":
        return "delete"
    if method in ("PUT", "PATCH"):
        return "update"
    if last.startswith("{") or last in SEGMENTS or last in SUB_NOUNS:
        return "create"
    return "action"


def describe_device(ua: str | None) -> str | None:
    """'iPhone · Safari', 'Windows · Chrome' from a user agent."""
    if not ua:
        return None
    if "iPhone" in ua:
        os_name = "iPhone"
    elif "iPad" in ua:
        os_name = "iPad"
    elif "Android" in ua:
        os_name = "Android"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "Mac"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = None
    if "Edg/" in ua:
        browser = "Edge"
    elif "CriOS" in ua or ("Chrome/" in ua and "Chromium" not in ua):
        browser = "Chrome"
    elif "FxiOS" in ua or "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    elif "python" in ua.lower() or "curl" in ua.lower() or "httpx" in ua.lower():
        browser = "Script"
    else:
        browser = None
    out = " · ".join(p for p in (os_name, browser) if p)
    return out or ua[:40]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:50]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip[:50]
    return request.client.host if request.client else None


_SECRET_KEY = re.compile(r"password|secret|token|api_key|authorization", re.I)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 25:
                out["…"] = f"{len(value) - 25} more"
                break
            out[k] = "•••" if _SECRET_KEY.search(str(k)) else _redact(v, depth + 1)
        return out
    if isinstance(value, list):
        items = [_redact(v, depth + 1) for v in value[:20]]
        if len(value) > 20:
            items.append(f"… {len(value) - 20} more")
        return items
    return _plain(value)


# ---------------------------------------------------------------------------
# Writing rows


class _Writer:
    """One background task per event loop that inserts queued rows in batches."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue | None = None
        self.task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def put(self, row: dict) -> None:
        loop = asyncio.get_running_loop()
        if self.loop is not loop or self.task is None or self.task.done():
            self.loop = loop
            self.queue = asyncio.Queue(maxsize=5000)
            self.task = loop.create_task(self._run())
        try:
            self.queue.put_nowait(row)
        except asyncio.QueueFull:
            logger.warning("activity log queue full; dropped a row")

    async def _run(self) -> None:
        assert self.queue is not None
        q = self.queue
        while True:
            row = await q.get()
            batch = [row]
            while not q.empty() and len(batch) < 100:
                batch.append(q.get_nowait())
            try:
                await _insert(batch)
            except Exception as e:
                logger.warning("activity log write failed (%d rows): %s", len(batch), e)
            finally:
                for _ in batch:
                    q.task_done()

    async def wait_idle(self) -> None:
        if self.queue is not None and self.loop is asyncio.get_running_loop():
            await self.queue.join()


_writer = _Writer()

# Tests point this at their own database.
session_factory = None
# Tests switch the IP lookup off.
GEO_LOOKUP = True

_users_cache: dict[str, tuple[float, tuple[str | None, str | None, str | None]]] = {}
_geo_cache: dict[str, str | None] = {}


def _factory():
    if session_factory is not None:
        return session_factory
    from app.db.engine import async_session_factory
    return async_session_factory


async def _location(ip: str | None) -> str | None:
    if not ip or not GEO_LOOKUP:
        return None
    if ip in _geo_cache:
        return _geo_cache[ip]
    loc = None
    try:
        from app.api.v1.listeners import _resolve_geo
        geo = await _resolve_geo(ip)
        parts = [geo.get("city"), geo.get("region"), geo.get("country")]
        seen: list[str] = []
        for p in parts:
            if p and p not in seen and p != "Unknown":
                seen.append(p)
        loc = ", ".join(seen)[:160] or None
    except Exception:
        loc = None
    if len(_geo_cache) > 5000:
        _geo_cache.clear()
    _geo_cache[ip] = loc
    return loc


async def _insert(batch: list[dict]) -> None:
    from app.models.asset import Asset
    from app.models.audit_log import AuditLog
    from app.models.user import User

    async with _factory()() as db:
        # Who: email, name and role (cached for a few minutes)
        now = time.monotonic()
        need = {r["user_id"] for r in batch if r.get("user_id") and not r.get("user_email")}
        need = {u for u in need if u not in _users_cache or now - _users_cache[u][0] > 300}
        if need:
            ids = []
            for u in need:
                try:
                    ids.append(uuid.UUID(u))
                except ValueError:
                    pass
            if ids:
                res = await db.execute(select(User.id, User.email, User.display_name, User.role).where(User.id.in_(ids)))
                for uid, email, name, role in res.all():
                    _users_cache[str(uid)] = (now, (email, name, role.value if hasattr(role, "value") else role))
        # Song titles for queue actions
        asset_ids: set[uuid.UUID] = set()
        for r in batch:
            for a in r.pop("_asset_ids", []) or []:
                try:
                    asset_ids.add(uuid.UUID(str(a)))
                except ValueError:
                    pass
        titles: dict[str, str] = {}
        if asset_ids:
            res = await db.execute(select(Asset.id, Asset.title).where(Asset.id.in_(list(asset_ids)[:200])))
            titles = {str(i): t for i, t in res.all() if t}

        for r in batch:
            uid = r.get("user_id")
            cached = _users_cache.get(uid) if uid else None
            if cached:
                email, name, role = cached[1]
                r.setdefault("user_email", email)
                if not r.get("user_email"):
                    r["user_email"] = email
                r.setdefault("user_name", name)
                if not r.get("user_role"):
                    r["user_role"] = role
            wanted = r.pop("_asset_names_for", None) or []
            off, on = r.pop("_went_off", None), r.pop("_went_on", None)
            if off or on:
                # "Song A → Song B": what Next or Play now took off air and put on
                r["detail"] = " → ".join(titles.get(a, "?") for a in (off, on) if a)[:500]
                if not off and on:
                    r["detail"] = f"On air: {titles.get(on, '?')}"
                extra = [a for a in wanted if a not in (off, on)]
                if extra:
                    added = ", ".join(dict.fromkeys(titles[a] for a in extra if a in titles))
                    if added:
                        r["detail"] = f"{added} · {r['detail']}"[:500]
            names = [titles[a] for a in wanted if a in titles]
            if names and not r.get("detail"):
                r["detail"] = ", ".join(dict.fromkeys(names))[:500]
            r["location"] = await _location(r.get("ip_address"))
            for k in ("user_id", "station_id"):
                if r.get(k):
                    try:
                        r[k] = uuid.UUID(str(r[k]))
                    except ValueError:
                        r[k] = None
            db.add(AuditLog(**{k: v for k, v in r.items() if not k.startswith("_")}))
        await db.commit()


# Tests: rows are kept here and written by the middleware once the request is
# done, through the test's own session (SQLite allows one writer at a time).
INLINE = False
_inline_rows: list[dict] = []


async def flush_inline() -> None:
    if not _inline_rows:
        return
    batch = list(_inline_rows)
    _inline_rows.clear()
    try:
        await _insert(batch)
    except Exception as e:
        logger.warning("activity log write failed (%d rows): %s", len(batch), e)


def enqueue(row: dict) -> None:
    """Queue one activity row (never raises)."""
    row.setdefault("created_at", dt.datetime.now(dt.timezone.utc))
    row.setdefault("updated_at", row["created_at"])
    if INLINE:
        _inline_rows.append(row)
        return
    try:
        _writer.put(row)
    except Exception as e:
        logger.warning("activity log enqueue failed: %s", e)


async def wait_idle() -> None:
    """Wait until queued rows are written."""
    await _writer.wait_idle()
    await flush_inline()


def record(
    request: Request | None,
    *,
    user_id: str | None,
    label: str,
    category: str,
    action: str,
    resource_type: str = "site",
    resource_id: str | None = None,
    detail: str | None = None,
    changes: dict | None = None,
    station_id: str | None = None,
    user_email: str | None = None,
    user_role: str | None = None,
    status_code: int | None = None,
) -> None:
    """Record something a route knows about directly (sign-ins, pages opened)."""
    ua = request.headers.get("user-agent") if request else None
    enqueue({
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "label": label[:200],
        "category": category,
        "action": action[:50],
        "resource_type": resource_type[:100],
        "resource_id": resource_id,
        "detail": detail,
        "changes": changes,
        "station_id": station_id,
        "method": request.method if request else None,
        "path": request.url.path[:300] if request else None,
        "status_code": status_code,
        "ip_address": client_ip(request) if request else None,
        "user_agent": (ua or "")[:400] or None,
        "device": describe_device(ua),
        "request_id": getattr(request.state, "request_id", None) if request else None,
    })


# ---------------------------------------------------------------------------
# Middleware


def _token_user(request: Request) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None, None
    try:
        from app.core.security import decode_token
        payload = decode_token(auth.split(" ", 1)[1].strip())
    except Exception:
        return None, None
    if payload.get("type") != "access":
        return None, None
    return payload.get("sub"), payload.get("role")


def _summary(objects: list[dict], counts: dict) -> str | None:
    names = [o["name"] for o in objects if o.get("name") and o["type"] not in ("queue_entries", "play_logs", "now_playing")]
    if names:
        return ", ".join(dict.fromkeys(names))[:500]
    return None


class ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if INLINE:
            try:
                return await self._dispatch(request, call_next)
            finally:
                await flush_inline()
        return await self._dispatch(request, call_next)

    async def _dispatch(self, request: Request, call_next):
        method = request.method
        path = request.url.path
        if method not in MUTATING or not path.startswith(API_PREFIX):
            return await call_next(request)
        sub_path = path[len(API_PREFIX):]
        if sub_path.startswith(EXCLUDED_PREFIXES) or sub_path.rstrip("/") + "/" in EXCLUDED_PREFIXES:
            return await call_next(request)
        user_id, role = _token_user(request)
        if not user_id:
            return await call_next(request)

        body: Any = None
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                raw = await request.body()
                if raw and len(raw) <= 64_000:
                    body = json.loads(raw)
            except Exception:
                body = None

        cap = _Capture()
        token = _capture.set(cap)
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            _capture.reset(token)
            try:
                self._queue_row(request, sub_path, user_id, role, body, cap, status)
            except Exception as e:
                logger.warning("activity row not queued: %s", e)

    @staticmethod
    def _queue_row(request: Request, sub_path: str, user_id: str, role: str | None,
                   body: Any, cap: _Capture, status: int) -> None:
        if status == 401:
            return
        route = request.scope.get("route")
        template = getattr(route, "path", None) or sub_path
        if template.startswith(API_PREFIX):
            template = template[len(API_PREFIX):]
        template = template.rstrip("/") or template
        method = request.method
        if (method, template) in EXCLUDED_EXACT:
            return
        label, category = describe(method, template)
        params = request.scope.get("path_params") or {}

        objects = []
        for item in cap.objects.values():
            item.pop("_obj", None)
            objects.append(item)

        station_id = params.get("station_id")
        if not station_id and isinstance(body, dict) and body.get("station_id"):
            station_id = body.get("station_id")
        if not station_id:
            for o in objects:
                if o.get("station_id"):
                    station_id = o["station_id"]
                    break
        if not station_id and objects and objects[0]["type"] == "stations":
            station_id = objects[0]["id"]

        # Which songs, for queue and control-room actions
        asset_ids: list[str] = []
        if isinstance(body, dict):
            for k in ("asset_id",):
                if body.get(k):
                    asset_ids.append(str(body[k]))
            for k in ("asset_ids",):
                if isinstance(body.get(k), list):
                    asset_ids.extend(str(a) for a in body[k][:10])
        went_off: str | None = None  # what was on air before, for Next / Play now
        went_on: str | None = None
        if category == "control":
            for o in objects:
                if o["type"] != "queue_entries" or not o.get("asset_id"):
                    continue
                status_change = (o.get("fields") or {}).get("status") or {}
                if status_change.get("old") == "playing" and status_change.get("new") != "playing":
                    went_off = o["asset_id"]
                elif status_change.get("new") == "playing":
                    went_on = o["asset_id"]
                elif o["op"] == "create":
                    asset_ids.append(o["asset_id"])
        asset_ids = list(dict.fromkeys(asset_ids))[:10]

        counts = {f"{t}:{op}": n for (t, op), n in cap.counts.items()}
        resource_type = template.strip("/").split("/")[0] if template.strip("/") else "site"
        resource_id = None
        for v in params.values():
            if v != station_id or len(params) == 1:
                resource_id = str(v)
        detail = _summary(objects, cap.counts)
        if not detail and isinstance(body, dict):
            for f in NAME_FIELDS:
                if isinstance(body.get(f), str) and body[f].strip():
                    detail = body[f].strip()[:200]
                    break

        changes: dict[str, Any] = {"objects": [{k: v for k, v in o.items() if k not in ("asset_id",)} for o in objects]}
        if counts:
            changes["counts"] = counts
        if body is not None:
            changes["request"] = _redact(body)
        if params:
            changes["params"] = {k: str(v) for k, v in params.items()}

        ua = request.headers.get("user-agent")
        enqueue({
            "user_id": user_id,
            "user_role": role,
            "label": label,
            "category": category,
            "action": action_kind(method, template),
            "resource_type": resource_type[:100],
            "resource_id": resource_id[:100] if resource_id else None,
            "detail": detail,
            "changes": changes,
            "station_id": str(station_id) if station_id else None,
            "method": method,
            "path": request.url.path[:300],
            "status_code": status,
            "ip_address": client_ip(request),
            "user_agent": (ua or "")[:400] or None,
            "device": describe_device(ua),
            "request_id": getattr(request.state, "request_id", None),
            "_asset_ids": asset_ids + [a for a in (went_off, went_on) if a],
            "_asset_names_for": asset_ids,
            "_went_off": went_off,
            "_went_on": went_on,
        })
