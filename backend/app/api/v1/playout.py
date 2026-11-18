"""
Endpoints called BY the playout engine (Liquidsoap on the stream server).

Authenticated with the shared secret in the `X-Playout-Token` header, not a user login.
"""

import logging
import time
import uuid

from fastapi import APIRouter, Body, Depends, Header, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.services import playout_engine, playout_plan, playout_tunnel

logger = logging.getLogger(__name__)

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
    # More kept reports follow at once: record this one quietly (no now-playing change)
    more: bool = False


def _require_engine(
    station_id: uuid.UUID, x_playout_token: str | None = Header(default=None)
) -> uuid.UUID:
    if not playout_engine.token_ok(x_playout_token):
        raise ForbiddenError("Invalid playout token")
    if not settings.engine_drives(station_id):
        raise NotFoundError("This station is not driven by the playout engine")
    return station_id


async def _one_engine(
    station_id: uuid.UUID = Depends(_require_engine),
    x_playout_instance: str | None = Header(default=None),
) -> uuid.UUID:
    """One engine per station: a second engine calling with this station's id is refused
    (409) and takes nothing from the queue (playout_engine.instance_ok)."""
    if not await playout_engine.instance_ok(station_id, x_playout_instance):
        raise ConflictError("Another playout engine plays this station")
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
    # How the box is doing (clock, disk, audio still to copy); see playout_tunnel.report_health
    health: dict | None = None


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
    playout_tunnel.report_health(station_id, body.health)
    return {"commands": await playout_tunnel.poll(station_id, body.wait)}


@router.post("/{station_id}/hello")
async def hello(
    station_id: uuid.UUID = Depends(_one_engine),
    body: HelloReport | None = Body(default=None),
    x_playout_instance: str | None = Header(default=None),
):
    """The engine announces that it (re)started, and what it understands (`proto`)."""
    await playout_engine.handle_hello(station_id, body.proto if body else None, x_playout_instance)
    return {"ok": True}


@router.get("/{station_id}/next")
async def next_track(
    station_id: uuid.UUID = Depends(_one_engine),
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
    station_id: uuid.UUID = Depends(_one_engine),
    db: AsyncSession = Depends(get_db),
):
    """The engine reports what just went on air."""
    t0 = time.monotonic()
    result = await playout_engine.handle_started(
        db, station_id, body.entry_id, body.lane, at=body.at, kept=body.kept,
        quiet=body.kept and body.more,
    )
    # Every start report's time, for the production log: only those over 3 s were
    # logged, so a regression under that went unseen (2026-11-18)
    logger.info("Start report station=%s entry=%s lane=%s answered in %d ms", station_id,
                body.entry_id or "-", body.lane or "-", (time.monotonic() - t0) * 1000)
    return result


class AlertReport(BaseModel):
    phase: str  # "started" or "ended"
    alert_id: str | None = None  # "" for the alert line
    source: str | None = None  # "api" or "line"
    at: float | None = None
    started_at: float | None = None


@router.post("/{station_id}/alert")
async def alert_report(
    body: AlertReport,
    station_id: uuid.UUID = Depends(_one_engine),
    db: AsyncSession = Depends(get_db),
):
    """The engine reports an emergency alert going on air or coming off (engine proto 6,
    wiki/emergency-alerts.md)."""
    from app.services import emergency_alerts

    if body.phase not in ("started", "ended"):
        return {"ok": False, "error": "phase must be started or ended"}
    alert = await emergency_alerts.engine_report(
        db, station_id, phase=body.phase, alert_id=body.alert_id or None, source=body.source,
        at=body.at, started_at=body.started_at,
    )
    await db.commit()
    return {"ok": alert is not None, "alert_id": str(alert.id) if alert else None}


# The show goes out as MP3 for the engine's live lane (agreed with the engine thread
# 2026-11-18: CBR 192 kbps, 44.1 kHz stereo; engine proto 5 plays it)
LIVE_MP3_ARGS = ("-f", "mp3", "-codec:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                 "-write_xing", "0", "-id3v2_version", "0", "-flush_packets", "1")


@router.get("/{station_id}/live.mp3")
async def live_audio(station_id: uuid.UUID = Depends(_require_token)):
    """The station's live show as listeners hear it (after the broadcast delay, Dumps
    applied, time checks included), for the playout engine's live lane and a local FM
    box's relay. 204 while no show is heard or about to be; otherwise an MP3 stream that ends when the
    show's delay has played out. Header token only, so it never lands in an access log."""
    import asyncio

    from fastapi.responses import StreamingResponse

    from app.api.v1.live_shows_ws import live_relay
    from app.services.live_air import RATE

    air = live_relay.airs.get(str(station_id))
    # Open too in the last second before the show is first heard (the engine is told
    # to go quiet then, `on_soon`): silence is sent until the first words. Answering
    # 204 until then made the engine come back a second later and connect only after
    # the show had begun: 2.3 s of silence on the stream at every take-over
    # (KBR Jerusalem, 2026-11-18).
    about_to = air is not None and air.told_soon and not air.finishing
    if air is None or air.closed or not (air.listening or about_to):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    tap = air.tap()

    async def stream():
        proc = await asyncio.create_subprocess_exec(
            # no probing and no input buffer: without these the first sound left 2 s late
            "ffmpeg", "-v", "error", "-fflags", "nobuffer", "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
            *LIVE_MP3_ARGS, "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )

        async def feed():
            try:
                started = False
                while True:
                    if started:
                        data = await tap.get()
                    else:
                        try:
                            data = await asyncio.wait_for(tap.get(), 0.1)
                        except asyncio.TimeoutError:
                            # Not heard yet: silence in real time keeps the reader connected
                            # and decoding, so the first words follow without a gap
                            proc.stdin.write(bytes(int(RATE * 0.1) * 2))
                            await proc.stdin.drain()
                            continue
                        started = True
                    if data is None:
                        break
                    proc.stdin.write(data)
                    await proc.stdin.drain()
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        feeder = asyncio.ensure_future(feed())
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            air.untap(tap)
            feeder.cancel()
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()

    return StreamingResponse(stream(), media_type="audio/mpeg",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                      # keeps the gzip middleware off it: gzip held the audio back
                                      # in 64 KB lumps (2.7 s) for a client that accepts gzip
                                      "Content-Encoding": "identity"})
