import uuid
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token, token_is_current
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


LAST_SEEN_EVERY_SECONDS = 60
_last_seen_written: dict = {}  # user id -> when this process last wrote last_seen_at

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
    if not token_is_current(payload, user):
        # The password or role changed since this token was made
        raise UnauthorizedError("Signed out: sign in again")

    # Track activity — throttle to avoid DB write on every request (max once per 5s)
    now = datetime.now(timezone.utc)
    last_seen = user.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes
    # At most once a minute per user, and only one request per process does it:
    # simultaneous requests used to update the same row and wait on each other.
    should_update = last_seen is None or (now - last_seen).total_seconds() > LAST_SEEN_EVERY_SECONDS
    previous_write = _last_seen_written.get(user.id)
    if should_update and previous_write is not None and (now - previous_write).total_seconds() < LAST_SEEN_EVERY_SECONDS:
        should_update = False
    if should_update:
        _last_seen_written[user.id] = now
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


def may_operate_station(user: User, station) -> bool:
    """Admins operate everything. Other staff operate the stations they are
    assigned to; with no assignment, every station that is not protected."""
    if user.role == UserRole.ADMIN:
        return True
    assigned = user.station_ids
    if station.protected:
        return assigned is not None and str(station.id) in assigned
    return assigned is None or str(station.id) in assigned


async def may_operate_every_station(db: AsyncSession, user: User) -> bool:
    """True for admins, and for staff allowed on every station (protected ones
    included). Something that applies to all stations, such as a spot schedule
    with no station, also airs on the protected FM station."""
    if user.role == UserRole.ADMIN:
        return True
    from app.models.station import Station

    stations = (await db.execute(select(Station))).scalars().all()
    return all(may_operate_station(user, s) for s in stations)


async def require_station_operator(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Router-level guard for /stations/{station_id}/... routes: anything that
    changes a station needs the caller to be allowed on THAT station. Reads are
    left to each route's own guard."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    raw = request.path_params.get("station_id")
    if not raw:
        return
    from app.models.station import Station

    if credentials is None:
        raise UnauthorizedError()
    user = await get_current_user(request, credentials, db)
    try:
        station_id = uuid.UUID(str(raw))
    except ValueError:
        return  # the route's own validation answers
    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    if station is not None and not may_operate_station(user, station):
        raise ForbiddenError("Your login is not set up to operate this station")


async def require_sponsor(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.SPONSOR:
        raise ForbiddenError("Sponsor access required")
    return user


async def require_sponsor_or_manager(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.SPONSOR):
        raise ForbiddenError("Sponsor or manager access required")
    return user
