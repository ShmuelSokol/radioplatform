"""
WebSocket endpoints for live show events and host audio streaming.
"""
import asyncio
import json
import logging
import uuid
from typing import Dict, Set

import numpy as np

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import decode_token
from app.db.session import get_async_session
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(tags=["live-shows-ws"])


# --- Live Show Event Manager ---

class LiveShowConnectionManager:
    """Manages WebSocket connections per live show for real-time event broadcasting."""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, show_id: str, websocket: WebSocket):
        await websocket.accept()
        if show_id not in self.active_connections:
            self.active_connections[show_id] = set()
        self.active_connections[show_id].add(websocket)
        logger.info(
            "Live show WS connected for show %s (total: %d)",
            show_id, len(self.active_connections[show_id]),
        )

    def disconnect(self, show_id: str, websocket: WebSocket):
        if show_id in self.active_connections:
            self.active_connections[show_id].discard(websocket)
            if not self.active_connections[show_id]:
                del self.active_connections[show_id]
        logger.info("Live show WS disconnected for show %s", show_id)

    async def broadcast_to_show(self, show_id: str, message: dict):
        """Broadcast a message to all connections for a show."""
        if show_id not in self.active_connections:
            return

        dead_connections = set()
        message_str = json.dumps(message)

        for connection in self.active_connections[show_id]:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logger.error("Failed to send to live show WS: %s", e)
                dead_connections.add(connection)

        for connection in dead_connections:
            self.disconnect(show_id, connection)


live_show_manager = LiveShowConnectionManager()


# --- Audio WebSocket Manager ---

class AudioConnectionManager:
    """Manages binary audio WebSocket connections from hosts."""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}  # show_id → single host ws

    async def connect(self, show_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[show_id] = websocket
        logger.info("Audio WS connected for show %s", show_id)

    def disconnect(self, show_id: str):
        self.active_connections.pop(show_id, None)
        logger.info("Audio WS disconnected for show %s", show_id)


audio_manager = AudioConnectionManager()


# --- On-air relay: the host's microphone straight to listeners ---
# There is no broadcast server (Icecast) yet, so the host's audio is passed on to
# every page listening to the station. Frames are raw 16-bit little-endian mono PCM
# at LIVE_SAMPLE_RATE (useHostAudio sends them; useLiveAudio plays them). One web
# process holds all sockets, like the now-playing channel.
LIVE_SAMPLE_RATE = 24000


# Web callers (see wiki/live-shows.md). A caller's microphone comes in on its own
# socket in the same format. The host's frames are the clock: each one is mixed
# with the same number of samples from every caller on air, and that mix is what
# listeners get, so listener pages need nothing new. The host hears the callers
# (sent back down the host's own socket) and each caller hears the host and the
# other callers, never themselves.
CALLER_PRIME_SAMPLES = int(LIVE_SAMPLE_RATE * 0.12)  # buffered before a caller is mixed in
CALLER_MAX_SAMPLES = int(LIVE_SAMPLE_RATE * 0.6)     # beyond this, the oldest audio is dropped


class CallerLine:
    def __init__(self, ws: WebSocket, call_id: str, show_id: str, station_id: str):
        self.ws = ws
        self.call_id = call_id
        self.show_id = show_id
        self.station_id = station_id
        self.on_air = False
        self.buf = np.zeros(0, dtype=np.int16)
        self.primed = False

    def add(self, frame: bytes) -> None:
        if not self.on_air or len(frame) < 2:
            return
        pcm = np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2")
        self.buf = np.concatenate((self.buf, pcm))[-CALLER_MAX_SAMPLES:]
        if len(self.buf) >= CALLER_PRIME_SAMPLES:
            self.primed = True

    def take(self, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.int32)
        if not self.on_air or not self.primed:
            return out
        got = self.buf[:n]
        out[: len(got)] = got
        self.buf = self.buf[n:]
        if len(self.buf) == 0:
            self.primed = False  # ran dry: wait for a little audio again before mixing
        return out


def _pcm(samples: np.ndarray) -> bytes:
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


class StationLiveRelay:
    def __init__(self):
        self.listeners: Dict[str, Set[WebSocket]] = {}  # station_id -> listener sockets
        self.on_air: Dict[str, str] = {}  # station_id -> show_id whose host is sending
        self.hosts: Dict[str, WebSocket] = {}  # station_id -> host audio socket
        self.callers: Dict[str, CallerLine] = {}  # call_id -> caller line

    def state(self, station_id: str) -> dict:
        show_id = self.on_air.get(station_id)
        return {"type": "live_state", "live": show_id is not None, "show_id": show_id,
                "sample_rate": LIVE_SAMPLE_RATE}

    async def _send_all(self, station_id: str, send) -> None:
        conns = list(self.listeners.get(station_id, ()))
        if not conns:
            return
        results = await asyncio.gather(*(send(ws) for ws in conns), return_exceptions=True)
        for ws, result in zip(conns, results):
            if isinstance(result, Exception):
                self.listeners.get(station_id, set()).discard(ws)

    async def set_on_air(self, station_id: str, show_id: str | None) -> None:
        if show_id is None:
            self.on_air.pop(station_id, None)
        else:
            self.on_air[station_id] = show_id
        message = json.dumps(self.state(station_id))
        await self._send_all(station_id, lambda ws: ws.send_text(message))

    def station_callers(self, station_id: str) -> list[CallerLine]:
        return [c for c in self.callers.values() if c.station_id == station_id]

    async def audio(self, station_id: str, frame: bytes) -> None:
        """A frame of the host's microphone: mix in the callers on air and send it on."""
        lines = self.station_callers(station_id)
        if not lines:
            await self._send_all(station_id, lambda ws: asyncio.wait_for(ws.send_bytes(frame), 2.0))
            return
        host = np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2").astype(np.int32)
        parts = {c.call_id: c.take(len(host)) for c in lines}
        callers_sum = np.zeros(len(host), dtype=np.int32)
        for part in parts.values():
            callers_sum += part
        mixed = _pcm(host + callers_sum)
        sends = [self._send_all(station_id, lambda ws: asyncio.wait_for(ws.send_bytes(mixed), 2.0))]
        host_ws = self.hosts.get(station_id)
        if host_ws is not None and any(c.on_air for c in lines):
            sends.append(self._send_quiet(host_ws, _pcm(callers_sum)))
        for c in lines:
            sends.append(self._send_quiet(c.ws, _pcm(host + callers_sum - parts[c.call_id])))
        await asyncio.gather(*sends)

    @staticmethod
    async def _send_quiet(ws: WebSocket, data: bytes | str) -> None:
        try:
            if isinstance(data, str):
                await asyncio.wait_for(ws.send_text(data), 2.0)
            else:
                await asyncio.wait_for(ws.send_bytes(data), 2.0)
        except Exception:
            pass  # its own socket handler notices the close

    async def caller_status(self, call_id: str, status: str) -> None:
        """The screener moved a web caller: mix them in (on air) or not, and tell them."""
        line = self.callers.get(call_id)
        if line is None:
            return
        line.on_air = status == "on_air"
        if not line.on_air:
            line.buf = np.zeros(0, dtype=np.int16)
            line.primed = False
        await self._send_quiet(line.ws, json.dumps({"type": "call_status", "status": status}))
        if status in ("rejected", "completed"):
            try:
                await line.ws.close(code=1000)
            except Exception:
                pass


live_relay = StationLiveRelay()


@router.websocket("/ws/live/station/{station_id}/listen")
async def websocket_station_live_listen(websocket: WebSocket, station_id: str):
    """Public: a listener page hears the station's live show here while one is on air."""
    await websocket.accept()
    live_relay.listeners.setdefault(station_id, set()).add(websocket)
    try:
        await websocket.send_text(json.dumps(live_relay.state(station_id)))
        while True:
            await websocket.receive_text()  # nothing expected; this only notices the close
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Live listen WS for station %s ended: %s", station_id, e)
    finally:
        live_relay.listeners.get(station_id, set()).discard(websocket)


# --- Auth Helper ---

async def _authenticate_ws(websocket: WebSocket, show_id: str) -> User | None:
    """
    Accept the WebSocket connection, then read the first message expecting
    {"type": "auth", "token": "<jwt>"}. Validate the JWT and verify the user
    has manager/admin role or is the show host.
    Returns the User on success, or None after closing the socket on failure.
    Close codes:
      4001 – invalid / missing token
      4003 – first message was not an auth message

    NOTE: this function calls websocket.accept() itself so that there is no
    window where the connection is open but unauthenticated.
    """
    try:
        await websocket.accept()
    except Exception as e:
        logger.error("WS auth: failed to accept connection for show %s: %s", show_id, e)
        return None

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
        logger.warning("WS auth: failed to read first message for show %s: %s", show_id, e)
        await websocket.close(code=4001)
        return None

    if msg.get("type") != "auth":
        logger.warning("WS auth: unexpected first message type '%s' for show %s", msg.get("type"), show_id)
        await websocket.close(code=4003)
        return None

    token = msg.get("token", "")
    try:
        payload = decode_token(token)
    except ValueError:
        logger.warning("WS auth: invalid token for show %s", show_id)
        await websocket.close(code=4001)
        return None

    if payload.get("type") != "access":
        logger.warning("WS auth: wrong token type for show %s", show_id)
        await websocket.close(code=4001)
        return None

    user_id_str = payload.get("sub")
    if not user_id_str:
        await websocket.close(code=4001)
        return None

    # Load user and check permissions
    try:
        async for db in get_async_session():
            result = await db.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
            user = result.scalar_one_or_none()

            if not user or not user.is_active:
                await websocket.close(code=4001)
                return None

            if user.role == UserRole.ADMIN:
                return user

            # A manager, or the show's own host, who may also operate the show's
            # station: a login not assigned to the protected FM station must not
            # put its microphone on that station's air (V1-148).
            from app.core.dependencies import may_operate_station
            from app.models.station import Station
            from app.services.live_show_service import get_show
            show = await get_show(db, uuid.UUID(str(show_id)))
            if show and (user.role == UserRole.MANAGER or show.host_user_id == user.id):
                station = await db.get(Station, show.station_id)
                if station is None or may_operate_station(user, station):
                    return user

            logger.warning("WS auth: user %s lacks permission for show %s", user.id, show_id)
            await websocket.close(code=4001)
            return None
    except Exception as e:
        logger.error("WS auth: DB error for show %s: %s", show_id, e)
        await websocket.close(code=4001)
        return None


# --- WebSocket Endpoints ---

@router.websocket("/ws/live/{show_id}/events")
async def websocket_live_events(websocket: WebSocket, show_id: str):
    """Real-time show state + caller updates WebSocket."""
    user = await _authenticate_ws(websocket, show_id)
    if user is None:
        return

    live_show_manager.active_connections.setdefault(show_id, set()).add(websocket)
    logger.info("Live show WS authed for show %s user %s (total: %d)", show_id, user.id, len(live_show_manager.active_connections[show_id]))

    try:
        # Send initial show state
        try:
            from app.db.session import get_async_session
            from app.services.live_show_service import get_show, get_show_calls, get_seconds_until_hard_stop
            from app.schemas.live_show import LiveShowInDB, CallInRequestInDB

            async for db in get_async_session():
                show = await get_show(db, show_id)
                if show:
                    calls = await get_show_calls(db, show_id)
                    await websocket.send_json({
                        "type": "show_state",
                        "data": {
                            "show": LiveShowInDB.model_validate(show).model_dump(mode="json"),
                            "callers": [
                                CallInRequestInDB.model_validate(c).model_dump(mode="json")
                                for c in calls
                            ],
                            "seconds_remaining": get_seconds_until_hard_stop(show),
                        },
                    })
                break
        except Exception as e:
            logger.error("Failed to send initial show state: %s", e)

        # Keep connection alive, send time_remaining every 10s
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                if data == "pong":
                    continue
                await websocket.send_json({"type": "pong", "data": data})
            except asyncio.TimeoutError:
                # Send time_remaining + keepalive
                try:
                    from app.db.session import get_async_session
                    from app.services.live_show_service import get_show, get_seconds_until_hard_stop

                    async for db in get_async_session():
                        show = await get_show(db, show_id)
                        if show:
                            seconds = get_seconds_until_hard_stop(show)
                            await websocket.send_json({
                                "type": "time_remaining",
                                "data": {"seconds": seconds},
                            })
                        else:
                            await websocket.send_json({"type": "ping"})
                        break
                except Exception:
                    await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        live_show_manager.disconnect(show_id, websocket)
    except Exception as e:
        logger.error("Live show WS error for show %s: %s", show_id, e)
        live_show_manager.disconnect(show_id, websocket)


@router.websocket("/ws/live/{show_id}/audio")
async def websocket_live_audio(websocket: WebSocket, show_id: str):
    """Binary audio WebSocket — receives MP3 chunks from host browser, forwards to Icecast."""
    user = await _authenticate_ws(websocket, show_id)
    if user is None:
        return

    station_id = None
    started = False
    try:
        async for db in get_async_session():
            from app.services.live_show_service import get_show, start_if_scheduled
            show = await get_show(db, show_id)
            if show is not None:
                station_id = str(show.station_id)
                # Broadcasting puts the show on air: pause the station's automation
                started = await start_if_scheduled(db, show.id, host_user_id=user.id)
                if started:
                    await db.commit()
            break
    except Exception as e:
        logger.error("Audio WS: could not load or start show %s: %s", show_id, e)
    if station_id is None:
        await websocket.close(code=4004)
        return
    if started:
        await broadcast_show_event(show_id, "show_started", {"show_id": show_id})

    audio_manager.active_connections[show_id] = websocket
    live_relay.hosts[station_id] = websocket  # hears the callers on air
    logger.info("Audio WS authed for show %s user %s (station %s)", show_id, user.id, station_id)
    await live_relay.set_on_air(station_id, show_id)

    from app.config import settings
    try:
        while True:
            data = await websocket.receive_bytes()
            await live_relay.audio(station_id, data)
            if settings.icecast_enabled:
                try:
                    from app.services.icecast_service import get_icecast_client
                    client = get_icecast_client(show_id)
                    await client.push_audio(data)
                except Exception as e:
                    logger.error("Failed to push audio to Icecast for show %s: %s", show_id, e)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Audio WS error for show %s: %s", show_id, e)
    finally:
        audio_manager.disconnect(show_id)
        if live_relay.hosts.get(station_id) is websocket:
            live_relay.hosts.pop(station_id, None)
        if live_relay.on_air.get(station_id) == show_id:
            await live_relay.set_on_air(station_id, None)


@router.websocket("/ws/live/call/{call_id}")
async def websocket_web_caller(websocket: WebSocket, call_id: str):
    """A web caller (public page): first message {"type":"auth","token":...} with the
    token the call-in request returned, then the caller's microphone as PCM frames.
    The caller hears the show here; their audio reaches listeners only while on air."""
    from app.services.call_in import call_token_ok, caller_left

    await websocket.accept()
    try:
        msg = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=15.0))
    except Exception:
        await websocket.close(code=4001)
        return
    if msg.get("type") != "auth" or not call_token_ok(call_id, str(msg.get("token", ""))):
        await websocket.close(code=4001)
        return

    from app.models.call_in_request import CallInRequest, CallStatus
    from app.models.live_show import LiveShow
    line = None
    status = None
    try:
        async for db in get_async_session():
            call = await db.get(CallInRequest, uuid.UUID(call_id))
            show = await db.get(LiveShow, call.live_show_id) if call else None
            if call is not None and show is not None:
                line = CallerLine(websocket, call_id, str(show.id), str(show.station_id))
                status = call.status.value if hasattr(call.status, "value") else str(call.status)
                line.on_air = status == CallStatus.ON_AIR.value
            break
    except Exception as e:
        logger.error("Caller WS: could not load call %s: %s", call_id, e)
    if line is None or status in ("rejected", "completed", "abandoned"):
        await websocket.close(code=4004)
        return

    old = live_relay.callers.get(call_id)
    live_relay.callers[call_id] = line  # a reconnect replaces the earlier socket
    if old is not None:
        try:
            await old.ws.close(code=1000)
        except Exception:
            pass
    logger.info("Web caller %s connected (show %s)", call_id, line.show_id)
    try:
        await websocket.send_text(json.dumps({
            "type": "call_status", "status": status, "sample_rate": LIVE_SAMPLE_RATE,
        }))
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                line.add(data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Caller WS %s ended: %s", call_id, e)
    finally:
        if live_relay.callers.get(call_id) is line:
            live_relay.callers.pop(call_id, None)
            await caller_left(call_id, line.show_id)


# --- Broadcast helpers ---

async def broadcast_show_event(show_id: str, event_type: str, data: dict):
    """Broadcast an event to all WebSocket clients connected to a show."""
    await live_show_manager.broadcast_to_show(show_id, {
        "type": event_type,
        "data": data,
    })
