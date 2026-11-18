"""
Endpoints called BY the playout engine (Liquidsoap on the stream server).

Authenticated with the shared secret in the `X-Playout-Token` header, not a user login.
"""

import uuid

from fastapi import APIRouter, Body, Depends, Header, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.session import get_db
from app.services import playout_engine, playout_plan, playout_tunnel

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
    # Set by a local FM box's relay on a report it kept while its line was down: `at` is
    # then when the item really went on air
    kept: bool = False


def _require_engine(
    station_id: uuid.UUID, x_playout_token: str | None = Header(default=None)
) -> uuid.UUID:
    if not playout_engine.token_ok(x_playout_token):
        raise ForbiddenError("Invalid playout token")
    if not settings.engine_drives(station_id):
        raise NotFoundError("This station is not driven by the playout engine")
    return station_id


def _require_token(
    station_id: uuid.UUID, x_playout_token: str | None = Header(default=None)
) -> uuid.UUID:
    """The shared secret alone: a local FM box copies the plan and the library before
    its station is switched over to it."""
    if not playout_engine.token_ok(x_playout_token):
        raise ForbiddenError("Invalid playout token")
    return station_id


@router.get("/{station_id}/plan")
async def plan(
    station_id: uuid.UUID = Depends(_require_token),
    hours: float = Query(default=48.0, gt=0, le=168),
    db: AsyncSession = Depends(get_db),
):
    """The pending queue with each item's engine URI, and the blackout windows: what a
    local FM box keeps so it can carry on with no internet (`wiki/local-fm-box.md`)."""
    return await playout_plan.build_plan(db, station_id, hours)


@router.get("/{station_id}/library")
async def library(
    station_id: uuid.UUID = Depends(_require_token),
    after: str | None = None,
    limit: int = Query(default=playout_plan.LIBRARY_PAGE, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """One page of every library item's audio address, for the box's full local copy."""
    try:
        return await playout_plan.library_page(db, after, limit)
    except ValueError:
        raise NotFoundError("Unknown page") from None


class CommandAnswer(BaseModel):
    id: str
    status: int = 0
    body: dict | None = None


class CommandPoll(BaseModel):
    wait: float = playout_tunnel.MAX_WAIT_SECONDS
    answers: list[CommandAnswer] = []


@router.post("/{station_id}/commands/poll")
async def commands_poll(
    body: CommandPoll,
    station_id: uuid.UUID = Depends(_require_engine),
):
    """A local FM box's relay collects the commands for its engine (Next, Play Now,
    timed hand-over) and hands back the engine's answers to the ones before. Held open
    until a command is made, so it arrives at once; no database session is held."""
    if not playout_tunnel.allowed(station_id):
        # Not named in PLAYOUT_RELAY_STATION_ID: a box with a wrong station id must not
        # take another station's commands away from its engine
        raise ForbiddenError("This station's commands do not go by relay")
    for a in body.answers:
        playout_tunnel.answer(station_id, a.id, a.status, a.body)
    return {"commands": await playout_tunnel.poll(station_id, body.wait)}


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
    return await playout_engine.handle_started(db, station_id, body.entry_id, body.lane, at=body.at if body.kept else None)
