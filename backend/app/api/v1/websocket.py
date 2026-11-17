"""
WebSocket endpoints for real-time updates.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.models.asset import stored_analysis
from app.services.scheduling import SchedulingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# A listener that cannot take a message within this time is dropped (it reconnects).
SEND_TIMEOUT = 5.0
# The scheduler re-announces the song on air every tick (3 s). Listeners only need
# a message when something changed; an unchanged one goes out at most this often.
UNCHANGED_RESEND_SECONDS = 30.0


# Connection manager for broadcasting now-playing updates
class ConnectionManager:
    def __init__(self):
        # Maps station_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, station_id: str, websocket: WebSocket):
        await websocket.accept()
        if station_id not in self.active_connections:
            self.active_connections[station_id] = set()
        self.active_connections[station_id].add(websocket)
        logger.info(f"WebSocket connected for station {station_id} (total: {len(self.active_connections[station_id])})")

    def disconnect(self, station_id: str, websocket: WebSocket):
        if station_id in self.active_connections:
            self.active_connections[station_id].discard(websocket)
            if not self.active_connections[station_id]:
                del self.active_connections[station_id]
        logger.info(f"WebSocket disconnected for station {station_id}")

    async def broadcast_to_station(self, station_id: str, message: dict):
        """Broadcast a message to all connections for a station."""
        if station_id not in self.active_connections:
            return
        message_str = json.dumps(message)
        connections = list(self.active_connections[station_id])

        async def send(connection):
            # One slow or stuck listener must not hold up everyone else's message.
            try:
                await asyncio.wait_for(connection.send_text(message_str), timeout=SEND_TIMEOUT)
                return None
            except Exception as e:
                logger.info("Dropping now-playing WebSocket: %s", e or type(e).__name__)
                return connection

        dead_connections = await asyncio.gather(*(send(c) for c in connections))
        for connection in dead_connections:
            if connection is not None:
                self.disconnect(station_id, connection)

manager = ConnectionManager()

# Separate manager for admin alert connections
class AlertConnectionManager:
    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.add(websocket)
        logger.info(f"Alert WebSocket connected (total: {len(self.connections)})")

    def disconnect(self, websocket: WebSocket):
        self.connections.discard(websocket)
        logger.info("Alert WebSocket disconnected")

    async def broadcast(self, message: dict):
        if not self.connections:
            return
        dead = set()
        message_str = json.dumps(message)
        for ws in set(self.connections):
            try:
                await ws.send_text(message_str)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.connections.discard(ws)

alert_manager = AlertConnectionManager()


ALERTS_AUTH_TIMEOUT = 5.0


async def alerts_ws_user(token: str, db):
    """The signed-in admin or manager a /ws/alerts token belongs to, else None.
    Alerts carry station names and error text, so only the roles that may read
    /alerts may listen (it used to accept anyone, with no token at all)."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.security import decode_token
    from app.models.user import User, UserRole

    try:
        payload = decode_token(token or "")
    except ValueError:
        return None
    if payload.get("type") != "access" or not payload.get("sub"):
        return None
    try:
        user_id = _uuid.UUID(str(payload["sub"]))
    except ValueError:
        return None
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active or user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        return None
    return user


@router.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    """Real-time alerts for admins and managers. The first message must be
    {"type": "auth", "token": "<access token>"}; otherwise the socket is closed
    (4003 not an auth message, 4001 bad token or role)."""
    from app.db.session import get_async_session

    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=ALERTS_AUTH_TIMEOUT)
    except Exception:
        await websocket.close(code=4003)
        return
    if not isinstance(first, dict) or first.get("type") != "auth":
        await websocket.close(code=4003)
        return
    user = None
    try:
        async for db in get_async_session():
            user = await alerts_ws_user(str(first.get("token", "")), db)
            break
    except Exception as e:
        logger.warning("Alert WebSocket auth failed: %s", e)
    if user is None:
        await websocket.close(code=4001)
        return
    alert_manager.connections.add(websocket)
    await websocket.send_json({"type": "authenticated"})
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                await websocket.send_json({"type": "pong", "data": data})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        alert_manager.disconnect(websocket)
    except Exception:
        alert_manager.disconnect(websocket)


async def broadcast_alert(alert_data: dict):
    """Broadcast an alert to all connected admin WebSocket clients."""
    await alert_manager.broadcast({
        "type": "alert",
        "data": alert_data,
    })


@router.websocket("/ws/now-playing/{station_id}")
async def websocket_now_playing(websocket: WebSocket, station_id: str):
    """
    WebSocket endpoint for real-time now-playing updates.
    Clients subscribe to a station and receive updates when playback changes.
    """
    try:
        await manager.connect(station_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket connect failed for station {station_id}: {e}", exc_info=True)
        return

    try:
        # Send initial state
        try:
            from app.db.session import get_async_session
            from sqlalchemy import select as sa_select, or_
            from app.models.queue_entry import QueueEntry
            from datetime import datetime, timezone

            # A station that plays from its queue: send exactly what the scheduler
            # broadcasts, so a listener who joins mid-song starts on the right song
            # (an unchanged now-playing message repeats only every 30 s).
            queue_payload = await _cached_queue_now_playing(station_id)
            if queue_payload is not None:
                await websocket.send_json({"type": "now_playing", "data": queue_payload})

            async for db in (get_async_session() if queue_payload is None else _no_session()):
                service = SchedulingService(db)
                now_playing = await service.get_now_playing(station_id)

                if now_playing:
                    asset = getattr(now_playing, "asset", None)
                    # Get audio analysis data
                    analysis = {}
                    audio_url = None
                    # Resolve the Supabase bucket name once, used for both current and next asset
                    supabase_bucket = getattr(settings, "SUPABASE_STORAGE_BUCKET", None)
                    if asset:
                        if getattr(asset, "metadata_extra", None):
                            analysis = stored_analysis(asset.metadata_extra)
                        file_path = asset.file_path or ""
                        if file_path.startswith("http://") or file_path.startswith("https://"):
                            audio_url = file_path
                        elif getattr(settings, "supabase_storage_enabled", False) and supabase_bucket:
                            audio_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{supabase_bucket}/{file_path}"

                    from app.api.v1.queue import is_crossfaded

                    duration = (getattr(asset, "duration", None) if asset else None) or 180.0

                    # Peek at next pending queue entry
                    now_utc = datetime.now(timezone.utc)
                    next_result = await db.execute(
                        sa_select(QueueEntry)
                        .where(
                            QueueEntry.station_id == station_id,
                            QueueEntry.status == "pending",
                            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= now_utc),
                        )
                        .order_by(QueueEntry.position)
                        .limit(1)
                    )
                    next_entry = next_result.scalar_one_or_none()
                    next_asset_data = None
                    if next_entry and getattr(next_entry, "asset", None):
                        na = next_entry.asset
                        na_analysis = {}
                        if getattr(na, "metadata_extra", None):
                            na_analysis = stored_analysis(na.metadata_extra)
                        na_file_path = getattr(na, "file_path", "") or ""
                        na_audio_url = None
                        if na_file_path.startswith("http://") or na_file_path.startswith("https://"):
                            na_audio_url = na_file_path
                        elif getattr(settings, "supabase_storage_enabled", False) and supabase_bucket:
                            na_audio_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{supabase_bucket}/{na_file_path}"
                        next_asset_data = {
                            "id": str(na.id),
                            "title": na.title,
                            "artist": na.artist,
                            "audio_url": na_audio_url,
                            "cue_in": na_analysis.get("cue_in_seconds", 0),
                            "replay_gain_db": na_analysis.get("replay_gain_db", 0),
                        }

                    started_at = getattr(now_playing, "started_at", None)
                    ends_at = getattr(now_playing, "ends_at", None)
                    asset_id = getattr(now_playing, "asset_id", None)
                    station_id_val = getattr(now_playing, "station_id", station_id)

                    await websocket.send_json({
                        "type": "now_playing",
                        "data": {
                            "station_id": str(station_id_val),
                            "asset_id": str(asset_id) if asset_id else None,
                            "started_at": started_at.isoformat() if started_at else None,
                            "ends_at": ends_at.isoformat() if ends_at else None,
                            "listener_count": getattr(now_playing, "listener_count", 0),
                            "stream_url": settings.public_stream_url(station_id),
                            "asset": {
                                "title": asset.title if asset else "Unknown",
                                "artist": getattr(asset, "artist", None) if asset else None,
                                "album": getattr(asset, "album", None) if asset else None,
                                "album_art_path": getattr(asset, "album_art_path", None) if asset else None,
                                "audio_url": audio_url,
                                "cue_in": analysis.get("cue_in_seconds", 0),
                                "cue_out": analysis.get("cue_out_seconds", duration),
                                "cross_start": analysis.get("cross_start_seconds", duration - 3.0),
                                # anything spoken is never crossfaded (see queue.overlap_seconds);
                                # for a song the page keeps its default until the next update
                                **({} if is_crossfaded(asset) else {"overlap_seconds": 0.0}),
                                "replay_gain_db": analysis.get("replay_gain_db", 0),
                            } if asset else None,
                            "next_asset": next_asset_data,
                        }
                    })
                else:
                    await websocket.send_json({"type": "now_playing", "data": None})
                break
        except WebSocketDisconnect:
            raise
        except Exception as e:
            logger.error(f"Error sending initial now-playing state for station {station_id}: {e}", exc_info=True)
            try:
                await websocket.send_json({"type": "error", "message": "Failed to load now-playing state"})
            except Exception:
                pass

        # Keep connection alive and handle incoming messages (if any)
        while True:
            try:
                # Wait for any client messages (e.g., ping/pong)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo back for testing
                await websocket.send_json({"type": "pong", "data": data})
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        manager.disconnect(station_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket error for station {station_id}: {e}")
        manager.disconnect(station_id, websocket)


# station_id -> (when, message data) for listeners joining. After a release every
# listener reconnects within seconds; without this each one would take a database
# connection (the pool has 15) and starve the scheduler and the control room.
_join_cache: Dict[str, tuple[float, dict]] = {}
_join_locks: Dict[str, asyncio.Lock] = {}
JOIN_CACHE_SECONDS = 5.0


async def _no_session():
    return
    yield


async def _cached_queue_now_playing(station_id: str, db=None) -> dict | None:
    """`db`: a request's own session to use on a cache miss (else a new one)."""
    from app.db.session import get_async_session

    hit = _join_cache.get(station_id)
    if hit and time.monotonic() - hit[0] < JOIN_CACHE_SECONDS:
        return hit[1]
    lock = _join_locks.setdefault(station_id, asyncio.Lock())
    async with lock:
        hit = _join_cache.get(station_id)
        if hit and time.monotonic() - hit[0] < JOIN_CACHE_SECONDS:
            return hit[1]
        generation = _np_generation.get(station_id, 0)
        payload = None
        if db is not None:
            payload = await _queue_now_playing(db, station_id)
        else:
            async for session in get_async_session():
                payload = await _queue_now_playing(session, station_id)
                break
        # An announcement during the read means it may be the old song: don't keep it
        if payload is not None and _np_generation.get(station_id, 0) == generation:
            _join_cache[station_id] = (time.monotonic(), payload)
        return payload


async def _queue_now_playing(db, station_id: str) -> dict | None:
    """The scheduler's now_playing message for the station's playing queue entry, if any."""
    import uuid as _uuid

    from sqlalchemy import select as sa_select
    from sqlalchemy.orm import joinedload

    from app.models.queue_entry import QueueEntry
    from app.services.scheduler_engine import get_scheduler

    try:
        sid = _uuid.UUID(str(station_id))
    except ValueError:
        return None
    entry = (
        await db.execute(
            sa_select(QueueEntry)
            .options(joinedload(QueueEntry.asset))
            .where(QueueEntry.station_id == sid, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc().nullslast())
            .limit(1)
        )
    ).unique().scalar_one_or_none()
    if entry is None or entry.asset is None:
        return None
    return await get_scheduler().queue_entry_payload(db, sid, entry)


async def broadcast_now_playing_update(station_id: str, now_playing_data: dict):
    """
    Helper function to broadcast now-playing updates to all connected clients.
    Called by the scheduling engine when playback changes.
    """
    started = _parse_start(now_playing_data.get("started_at"))
    latest = _latest_start.get(station_id)
    if started is not None and latest is not None and started < latest:
        # Something that started before what listeners already hear: a stale read
        # (a scheduler tick that read the old song just before the change, then
        # broadcast after it). Sending it would jump every listener back.
        logger.warning(
            "Dropped stale now_playing for station %s (started %s, on air since %s)",
            station_id, started.isoformat(), latest.isoformat(),
        )
        return
    if started is not None:
        _latest_start[station_id] = started
    _np_generation[station_id] = _np_generation.get(station_id, 0) + 1
    message = {"type": "now_playing", "data": now_playing_data}
    key = json.dumps(message, sort_keys=True, default=str)
    now = time.monotonic()
    last = _last_now_playing.get(station_id)
    if last and last[0] == key and now - last[1] < UNCHANGED_RESEND_SECONDS:
        return
    _last_now_playing[station_id] = (key, now)
    _join_cache.pop(station_id, None)  # the next listener to join reads the new song
    await manager.broadcast_to_station(station_id, message)


async def broadcast_nothing_playing(station_id) -> None:
    """Tell listeners the station has nothing on air any more (the last item ended
    or was skipped or removed and nothing followed it, e.g. during a blackout with no
    silence queued). Without it open pages kept showing the old item "On Air" until
    their overdue re-read. Sends {"type": "now_playing", "data": null}."""
    station_id = str(station_id)
    _np_generation[station_id] = _np_generation.get(station_id, 0) + 1
    _join_cache.pop(station_id, None)
    message = {"type": "now_playing", "data": None}
    key = json.dumps(message)
    now = time.monotonic()
    last = _last_now_playing.get(station_id)
    if last and last[0] == key and now - last[1] < UNCHANGED_RESEND_SECONDS:
        return
    _last_now_playing[station_id] = (key, now)
    try:
        await manager.broadcast_to_station(station_id, message)
    except Exception as e:
        logger.warning("Nothing-playing broadcast failed for station %s: %s", station_id, e)


# station_id -> (last message sent, when)
_last_now_playing: Dict[str, tuple[str, float]] = {}
# station_id -> start time of the latest item announced (never goes backwards)
_latest_start: Dict[str, datetime] = {}
# station_id -> count of announcements, so a join-cache fill that raced one is not kept
_np_generation: Dict[str, int] = {}


def _parse_start(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
