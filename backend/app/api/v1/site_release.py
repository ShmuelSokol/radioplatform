"""Admin control over when open browser tabs move onto a new website release.

Every open tab keeps a WebSocket here (/ws/site). It tells the server which
release it runs, which screen it is on and whether it is playing audio, and the
server tells it the release policy the moment an admin changes it:

- mode "auto": each tab reloads itself at its first quiet moment (no audio).
- mode "manual": tabs stay on what they have until an admin pushes.
- a push: tabs take the release at the given time (or now). Tabs playing audio
  wait until they stop, unless the push includes them: then they reload at the
  next item change and carry on playing where the station is.

The tab side is frontend/src/lib/appUpdate.ts. See wiki/release-pickup.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin
from app.db.session import get_db
from app.models.site_release import SiteRelease
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["site-release"])

ROW = "site"
SEND_TIMEOUT = 5.0
# A tab says hello at least this often; one silent for longer is dropped.
TAB_SILENT_LIMIT = 150.0


class Tab:
    __slots__ = ("ws", "info", "connected_at", "seen_at")

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.info: dict = {}
        self.connected_at = time.time()
        self.seen_at = time.time()


_tabs: dict[int, Tab] = {}


def _state(row: SiteRelease | None) -> dict:
    push = None
    if row is not None and row.push_id:
        push = {
            "id": row.push_id,
            "version": row.push_version,
            "at": row.push_at.isoformat() if row.push_at else None,
            "include_playing": bool(row.push_include_playing),
            "pushed_at": row.pushed_at.isoformat() if row.pushed_at else None,
            "pushed_by": row.pushed_by,
        }
    return {
        "mode": (row.mode if row is not None else "auto") or "auto",
        "push": push,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


async def _row(db: AsyncSession) -> SiteRelease | None:
    return (await db.execute(select(SiteRelease).where(SiteRelease.name == ROW))).scalar_one_or_none()


async def _row_for_update(db: AsyncSession) -> SiteRelease:
    row = await _row(db)
    if row is None:
        row = SiteRelease(name=ROW, mode="auto", push_include_playing=False)
        db.add(row)
    return row


async def _broadcast(state: dict) -> None:
    message = json.dumps({"type": "release", "data": state})

    async def send(tab: Tab):
        try:
            await asyncio.wait_for(tab.ws.send_text(message), timeout=SEND_TIMEOUT)
        except Exception:
            _tabs.pop(id(tab.ws), None)

    await asyncio.gather(*(send(t) for t in list(_tabs.values())))


@router.get("/site-release")
async def get_site_release(db: AsyncSession = Depends(get_db)):
    """The release policy. Public: every tab needs it, and it holds nothing private."""
    return _state(await _row(db))


class ModeIn(BaseModel):
    mode: Literal["auto", "manual"]


@router.put("/site-release")
async def set_mode(body: ModeIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    row = await _row_for_update(db)
    row.mode = body.mode
    await db.commit()
    state = _state(row)
    await _broadcast(state)
    return state


class PushIn(BaseModel):
    # The release tabs should move to (the "version" in kbrlive.com/version.json).
    # Leave it out to mean "whatever is published": then the push lasts one hour.
    version: str | None = Field(default=None, max_length=80)
    # When tabs take it. Leave out for now.
    at: datetime | None = None
    # Also reload tabs that are playing audio, at their next item change.
    include_playing: bool = False


@router.post("/site-release/push")
async def push_release(body: PushIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    at = body.at or now
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    row = await _row_for_update(db)
    row.push_id = uuid.uuid4().hex
    row.push_version = body.version or None
    row.push_at = at
    row.push_include_playing = body.include_playing
    row.pushed_at = now
    row.pushed_by = user.display_name or user.email
    await db.commit()
    state = _state(row)
    await _broadcast(state)
    logger.info("Site release pushed by %s: version=%s at=%s include_playing=%s",
                row.pushed_by, row.push_version, at.isoformat(), body.include_playing)
    return state


@router.delete("/site-release/push")
async def cancel_push(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    row = await _row_for_update(db)
    row.push_id = None
    row.push_version = None
    row.push_at = None
    row.push_include_playing = False
    await db.commit()
    state = _state(row)
    await _broadcast(state)
    return state


@router.get("/site-release/tabs")
async def list_tabs(user: User = Depends(require_admin)):
    """Every open tab of the site right now: its release, screen and whether it plays audio."""
    now = time.time()
    tabs = []
    for tab in list(_tabs.values()):
        if now - tab.seen_at > TAB_SILENT_LIMIT:
            continue
        info = tab.info
        tabs.append({
            "client_id": info.get("client_id"),
            "version": info.get("version"),
            "page": info.get("page"),
            "playing": bool(info.get("playing")),
            "browser": info.get("browser"),
            "user": info.get("user"),
            "waiting": info.get("waiting"),
            "connected_seconds": round(now - tab.connected_at),
        })
    tabs.sort(key=lambda t: (t["page"] or "", t["client_id"] or ""))
    return {"tabs": tabs}


ALLOWED_KEYS = {"client_id", "version", "page", "playing", "browser", "user", "waiting"}


def _clean(info: dict) -> dict:
    out = {}
    for key in ALLOWED_KEYS:
        value = info.get(key)
        if isinstance(value, str):
            value = value[:200]
        elif not isinstance(value, (bool, int, float)) and value is not None:
            continue
        out[key] = value
    return out


@router.websocket("/ws/site")
async def site_socket(websocket: WebSocket):
    await websocket.accept()
    tab = Tab(websocket)
    _tabs[id(websocket)] = tab
    try:
        try:
            from app.db.engine import async_session_factory
            async with async_session_factory() as db:
                state = _state(await _row(db))
        except Exception:
            logger.warning("site socket: release policy unavailable", exc_info=True)
            state = _state(None)
        await websocket.send_text(json.dumps({"type": "release", "data": state}))
        while True:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=TAB_SILENT_LIMIT)
            tab.seen_at = time.time()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "tab" and isinstance(msg.get("data"), dict):
                tab.info = _clean(msg["data"])
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        logger.info("site socket closed", exc_info=True)
    finally:
        _tabs.pop(id(websocket), None)
