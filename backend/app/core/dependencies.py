import uuid
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserRole

bearer_scheme = HTTPBearer()

# Map request paths to human-readable actions
_ACTION_MAP = [
    ("GET", "/assets", "Browsed library"),
    ("POST", "/assets/upload", "Uploaded asset"),
    ("POST", "/assets/backfill", "Ran backfill"),
    ("GET", "/assets/", "Viewed asset"),
    ("PATCH", "/assets/", "Edited asset"),
    ("DELETE", "/assets/", "Deleted asset"),
    ("GET", "/stations", "Viewed stations"),
    ("GET", "/queue", "Viewed queue"),
    ("POST", "/queue", "Modified queue"),
    ("GET", "/rules", "Viewed rules"),
    ("GET", "/schedules", "Viewed schedules"),
    ("GET", "/users", "Viewed users"),
    ("GET", "/sponsors", "Viewed sponsors"),
    ("GET", "/alerts", "Viewed alerts"),
    ("GET", "/analytics", "Viewed analytics"),
    ("GET", "/now-playing", "Checked now playing"),
    ("GET", "/song-requests", "Viewed requests"),
    ("POST", "/song-requests", "Managed request"),
    ("GET", "/live-shows", "Viewed live shows"),
    ("GET", "/archives", "Viewed archives"),
    ("GET", "/holidays", "Viewed holidays"),
    ("GET", "/playlists", "Viewed playlists"),
    ("GET", "/listeners", "Viewed listeners"),
]


def _classify_action(method: str, path: str) -> str:
    """Classify a request into a human-readable action."""
    for m, prefix, label in _ACTION_MAP:
        if method == m and prefix in path:
            return label
    if method == "GET":
        return "Browsed dashboard"
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "Made changes"
    return "Active"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise UnauthorizedError()

    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError()

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    # Track activity — throttle to avoid DB write on every request (max once per 5s)
    now = datetime.now(timezone.utc)
    last_seen = user.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes
    should_update = last_seen is None or (now - last_seen).total_seconds() > 5
    if should_update:
        action = _classify_action(request.method, request.url.path)
        user.last_seen_at = now
        user.last_action = action
        try:
            await db.flush()
        except Exception:
            pass  # Don't fail requests over activity tracking

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return user


async def require_manager(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise ForbiddenError("Manager access required")
    return user


async def require_dj_or_manager(user: User = Depends(get_current_user)) -> User:
    """Allow admin, manager, or DJ roles — for live shows, queue, playback controls."""
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.DJ):
        raise ForbiddenError("DJ or manager access required")
    return user


async def require_staff(user: User = Depends(get_current_user)) -> User:
    """Any station staff role (admin, manager, DJ, viewer). Keeps sponsor
    accounts out of the library, queue, rules and review screens."""
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.DJ, UserRole.VIEWER):
        raise ForbiddenError("Staff access required")
    return user


async def require_sponsor(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.SPONSOR:
        raise ForbiddenError("Sponsor access required")
    return user


async def require_sponsor_or_manager(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.SPONSOR):
        raise ForbiddenError("Sponsor or manager access required")
    return user
