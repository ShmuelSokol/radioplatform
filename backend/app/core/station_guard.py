"""Station permissions for routes that do not carry the station in the path.

`require_station_operator` (dependencies.py) covers /stations/{station_id}/...
Schedules, playlists, spot schedules and live shows name the station
in the query or body, or only through the object being changed. `station_write_guard`
works out which station(s) a write touches and refuses it when the caller may
not operate one of them. Reads are never restricted here, and a request with no
login is left to the route's own guard (webhooks stay reachable).

An object that belongs to no station (applies to all of them) is not restricted.
"""
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, may_operate_station
from app.core.exceptions import ForbiddenError
from app.db.session import get_db
from app.models.user import UserRole

Resolver = Callable[[AsyncSession, uuid.UUID], Awaitable[uuid.UUID | None]]


async def _column(db: AsyncSession, column, model, key: uuid.UUID):
    return (await db.execute(select(column).where(model.id == key))).scalar_one_or_none()


async def station(db: AsyncSession, key: uuid.UUID):
    return key


async def of_schedule(db: AsyncSession, key: uuid.UUID):
    from app.models.schedule import Schedule

    return await _column(db, Schedule.station_id, Schedule, key)


async def of_block(db: AsyncSession, key: uuid.UUID):
    from app.models.schedule_block import ScheduleBlock

    schedule_id = await _column(db, ScheduleBlock.schedule_id, ScheduleBlock, key)
    return await of_schedule(db, schedule_id) if schedule_id else None


async def of_playlist_entry(db: AsyncSession, key: uuid.UUID):
    from app.models.playlist_entry import PlaylistEntry

    block_id = await _column(db, PlaylistEntry.block_id, PlaylistEntry, key)
    return await of_block(db, block_id) if block_id else None


async def of_template(db: AsyncSession, key: uuid.UUID):
    from app.models.playlist_template import PlaylistTemplate

    return await _column(db, PlaylistTemplate.station_id, PlaylistTemplate, key)


async def of_template_slot(db: AsyncSession, key: uuid.UUID):
    from app.models.playlist_template import TemplateSlot

    template_id = await _column(db, TemplateSlot.template_id, TemplateSlot, key)
    return await of_template(db, template_id) if template_id else None


async def of_spot_schedule(db: AsyncSession, key: uuid.UUID):
    from app.models.spot_schedule import SpotSchedule

    return await _column(db, SpotSchedule.station_id, SpotSchedule, key)


async def of_live_show(db: AsyncSession, key: uuid.UUID):
    from app.models.live_show import LiveShow

    return await _column(db, LiveShow.station_id, LiveShow, key)


def _ids(value) -> list[uuid.UUID]:
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        try:
            out.append(uuid.UUID(str(v)))
        except (ValueError, TypeError):
            pass  # the route's own validation answers
    return out


def station_write_guard(*, path: dict[str, Resolver] | None = None, body: dict[str, Resolver] | None = None):
    """Router-level dependency. `path` maps path parameter names, and `body`
    maps JSON or form body or query field names, to the resolver that turns that id
    into a station id. `station_id` in the query or body is always checked."""
    path = path or {}
    fields = {"station_id": station, **(body or {})}

    async def guard(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if request.method in ("GET", "HEAD", "OPTIONS") or credentials is None:
            return
        user = await get_current_user(request, credentials, db)
        if user.role == UserRole.ADMIN:
            return

        found: list[tuple[Resolver, uuid.UUID]] = []
        for name, resolver in path.items():
            if name in request.path_params:
                found += [(resolver, k) for k in _ids(request.path_params[name])]
        payload = {}
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
        elif "form" in content_type:  # multipart or url-encoded: a form write names the station too
            try:
                form = await request.form()
                payload = {k: (form.getlist(k) if len(form.getlist(k)) > 1 else form.get(k)) for k in form.keys()}
            except Exception:
                payload = {}
        for name, resolver in fields.items():
            if name in request.query_params:
                found += [(resolver, k) for k in _ids(request.query_params[name])]
            if isinstance(payload, dict) and payload.get(name) is not None:
                found += [(resolver, k) for k in _ids(payload[name])]

        from app.models.station import Station

        checked: set[uuid.UUID] = set()
        for resolver, key in found:
            station_id = await resolver(db, key)
            if station_id is None or station_id in checked:
                continue
            checked.add(station_id)
            row = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
            if row is not None and not may_operate_station(user, row):
                raise ForbiddenError("Your login is not set up to operate this station")

    return guard
