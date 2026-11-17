"""
Endpoints called BY the playout engine (Liquidsoap on the stream server).

Authenticated with the shared secret in the `X-Playout-Token` header, not a user login.
"""

import uuid

from fastapi import APIRouter, Body, Depends, Header, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.session import get_db
from app.services import playout_engine

router = APIRouter(prefix="/playout", tags=["playout"])


class HelloReport(BaseModel):
    # What the engine's radio.liq understands (see playout_engine.ENGINE_PROTO_GROUPS);
    # an old engine sends nothing
    proto: int | None = None


class StartedReport(BaseModel):
    entry_id: str | None = None
    lane: str | None = None
    title: str | None = None
    at: float | None = None


def _require_engine(
    station_id: uuid.UUID, x_playout_token: str | None = Header(default=None)
) -> uuid.UUID:
    if not playout_engine.token_ok(x_playout_token):
        raise ForbiddenError("Invalid playout token")
    if not settings.engine_drives(station_id):
        raise NotFoundError("This station is not driven by the playout engine")
    return station_id


@router.post("/{station_id}/hello")
async def hello(
    station_id: uuid.UUID = Depends(_require_engine),
    body: HelloReport | None = Body(default=None),
):
    """The engine announces that it (re)started, and what it understands (`proto`)."""
    await playout_engine.handle_hello(station_id, body.proto if body else None)
    return {"ok": True}


@router.get("/{station_id}/next")
async def next_track(
    station_id: uuid.UUID = Depends(_require_engine),
    given: str | None = None,
    proto: int | None = None,
    live: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """What should play next. 204 when the queue is empty (the engine retries).

    `given` is the last few entry ids the engine was handed, newest first, comma
    separated. They are never handed out again, and it lets the backend catch up if
    a start report was lost.
    """
    ids = [g for g in (given or "").split(",") if g]
    result = await playout_engine.handle_next(db, station_id, ids, proto=proto, engine_live_on=bool(live))
    if result is None:
        return Response(status_code=204)
    return result


@router.post("/{station_id}/started")
async def started(
    body: StartedReport,
    station_id: uuid.UUID = Depends(_require_engine),
    db: AsyncSession = Depends(get_db),
):
    """The engine reports what just went on air."""
    return await playout_engine.handle_started(db, station_id, body.entry_id, body.lane)
