"""
WebSocket endpoints for live show events and host audio streaming.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Set

import numpy as np

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import decode_token, token_is_current
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
        # Talkback: the screener or host talking to this caller off the air. While
        # set, the caller hears that person (not the show) and they hear the caller
        self.talk: WebSocket | None = None

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
        # station_id -> the broadcast delay listeners hear the show through (services/live_air.py)
        self.airs: Dict[str, Any] = {}
        # Listener sockets that asked for no audio (their page plays the show from the stream)
        self.no_audio: Set[WebSocket] = set()
        self.quieted_early: set[str] = set()   # engine stations told to go quiet before first air
        # station_id -> called once when listeners first hear the show (takes the station over)
        self.on_first_air: Dict[str, object] = {}

    def state(self, station_id: str) -> dict:
        air = self.airs.get(station_id)
        live = air is not None and air.listening
        return {"type": "live_state", "live": live, "show_id": air.show_id if live else None,
                "sample_rate": LIVE_SAMPLE_RATE, "on_stream": self.on_stream(station_id)}

    @staticmethod
    def on_stream(station_id: str) -> bool:
        """The station's public stream carries the show itself (engine proto 5 plays
        /playout/{id}/live.mp3): a page playing that stream must not also play the
        frames sent here, or the host is heard twice, seconds apart."""
        from app.config import settings
        from app.services import playout_engine

        return bool(settings.engine_drives(station_id) and settings.public_stream_url(station_id)
                    and playout_engine.get_state(station_id).engine_proto >= playout_engine.ENGINE_PROTO_LIVE_AUDIO)

    async def _send_all(self, station_id: str, send) -> None:
        conns = list(self.listeners.get(station_id, ()))
        if not conns:
            return
        results = await asyncio.gather(*(send(ws) for ws in conns), return_exceptions=True)
        for ws, result in zip(conns, results):
            if isinstance(result, Exception):
                self.listeners.get(station_id, set()).discard(ws)

    async def _send_frame(self, station_id: str, data: bytes) -> None:
        conns = [ws for ws in self.listeners.get(station_id, ()) if ws not in self.no_audio]
        if not conns:
            return
        results = await asyncio.gather(*(asyncio.wait_for(ws.send_bytes(data), 2.0) for ws in conns),
                                       return_exceptions=True)
        for ws, result in zip(conns, results):
            if isinstance(result, Exception):
                self.listeners.get(station_id, set()).discard(ws)

    async def broadcast_state(self, station_id: str) -> None:
        message = json.dumps(self.state(station_id))
        await self._send_all(station_id, lambda ws: ws.send_text(message))

    async def set_on_air(
        self, station_id: str, show_id: str | None, delay: float = 0.0, dump: float = 0.0,
        end_at: float | None = None, drain: bool = True, on_done=None, heard_now: bool = False,
        record: bool = False,
    ) -> None:
        """A host starts sending for `show_id` (listeners hear it `delay` seconds later),
        or (show_id None) the show is over: what is still in the delay plays out first
        when `drain`, then `on_done` runs and listeners are told it is off air."""
        from app.services.live_air import LiveAir

        air = self.airs.get(station_id)
        if show_id is None:
            self.on_air.pop(station_id, None)
            if air is None:
                await self.broadcast_state(station_id)
                if on_done is not None:
                    await on_done()
                return
            if on_done is not None:
                air.on_done = on_done
            air.finish("ended")
            if not drain or air.drained() or not air.listening:
                await air.stop()
            return

        self.on_air[station_id] = show_id
        if air is not None and air.show_id == show_id and not air.closed \
                and (not air.finishing or air.finish_reason == "host left"):
            # The host came back (a reload, a network drop): carry on in the same delay
            air.finishing = False
            air.finish_reason = ""
            air.enter_delay()
            air.end_at = end_at
            return
        if air is not None:
            await air.stop()

        async def send(data: bytes, _station=station_id) -> None:
            await self._send_frame(_station, data)

        async def listening(on: bool, _station=station_id, _show=show_id) -> None:
            await self.broadcast_state(_station)
            if on:
                first = self.on_first_air.pop(_station, None)
                if first is not None:
                    await first(_station, _show)

        async def status(data: dict, _show=show_id) -> None:
            await live_show_manager.broadcast_to_show(_show, {"type": "air_status", "data": data})

        async def soon(_station=station_id) -> None:
            # The stream server goes quiet a moment before the host is heard: told only
            # at first air it played music under the host's first 2.5 s
            from app.config import settings as _settings
            if _settings.engine_drives(_station):
                from app.services import playout_engine
                self.quieted_early.add(_station)
                await playout_engine.engine_live(_station, True)

        new = LiveAir(station_id, show_id, send, delay=delay, dump=dump,
                      on_listening=listening, on_status=status, on_soon=soon)
        new.end_at = end_at

        async def done(_station=station_id, _air=new) -> None:
            if self.airs.get(_station) is _air:
                self.airs.pop(_station, None)  # listeners were told when it stopped airing

        async def closed(_station=station_id, _air=new) -> None:
            if _station in self.quieted_early:
                self.quieted_early.discard(_station)
                if not _air.ever_heard:
                    # Ended in the last second of its pre-roll: nothing took the station
                    # over, so nothing else would give the stream its sound back
                    from app.services import playout_engine
                    await playout_engine.engine_live(_station, False)
            from app.services import scheduler_engine as _sched
            if _air.finish_reason == "host left" and _air.ever_heard \
                    and _sched._scheduler_instance is not None and _sched._scheduler_instance.running:
                # The last words of a host who left have played out: look now whether
                # the station goes back to music, not on the scheduler's next pass
                # (up to 3 s more silence; harness measured 3.6 to 3.9 s)
                task = asyncio.ensure_future(_host_gone_now(_station, _air.show_id))
                _holds.add(task)
                task.add_done_callback(_holds.discard)

        new.on_closed = closed

        new.on_done = done
        self.airs[station_id] = new
        if new.target == 0 or heard_now:
            # No delay: on air at once, like before the delay existed. Or the station is
            # already the show's (the host came back after a drop): on air at once and
            # the delay builds back while they talk, with no second pre-roll of silence
            new.listening = True
            new.ever_heard = True
            new.mode = "no delay" if new.target == 0 else "building"
            await listening(True)
        new.start()
        if record:
            try:
                from app.services import live_recording
                live_recording.start(station_id, show_id, new)
            except Exception:
                logger.warning("Could not start recording live show %s", show_id, exc_info=True)

    async def host_left(self, station_id: str, show_id: str) -> None:
        """The host's page went away (the show goes on): play out what is in the delay."""
        if self.on_air.get(station_id) == show_id:
            self.on_air.pop(station_id, None)
        air = self.airs.get(station_id)
        if air is None or air.show_id != show_id:
            await self.broadcast_state(station_id)
            return
        air.finish("host left")
        if air.drained() or not air.listening:
            await air.stop()

    def station_callers(self, station_id: str) -> list[CallerLine]:
        return [c for c in self.callers.values() if c.station_id == station_id]

    async def audio(self, station_id: str, frame: bytes) -> None:
        """A frame of the host's microphone: mix in the callers on air and send it on."""
        lines = self.station_callers(station_id)
        if not lines:
            await self._to_listeners(station_id, frame)
            return
        host = np.frombuffer(frame[: len(frame) // 2 * 2], dtype="<i2").astype(np.int32)
        parts = {c.call_id: c.take(len(host)) for c in lines}
        callers_sum = np.zeros(len(host), dtype=np.int32)
        for part in parts.values():
            callers_sum += part
        mixed = _pcm(host + callers_sum)
        sends = [self._to_listeners(station_id, mixed)]
        host_ws = self.hosts.get(station_id)
        if host_ws is not None and any(c.on_air for c in lines):
            sends.append(self._send_quiet(host_ws, _pcm(callers_sum)))
        for c in lines:
            if c.talk is None:   # in a talkback the caller hears the screener, not the show
                sends.append(self._send_quiet(c.ws, _pcm(host + callers_sum - parts[c.call_id])))
        await asyncio.gather(*sends)

    async def talk_start(self, call_id: str, ws: WebSocket) -> bool:
        """A screener or the host starts talking to a caller off the air. False when
        the caller's page is not connected or the caller is on air."""
        line = self.callers.get(call_id)
        if line is None or line.on_air:
            return False
        old = line.talk
        line.talk = ws
        if old is not None and old is not ws:
            try:
                await old.close(code=4000)   # someone else took the caller over
            except Exception:
                pass
        await self._send_quiet(line.ws, json.dumps({"type": "talk", "on": True}))
        return True

    async def talk_end(self, call_id: str, ws: WebSocket | None = None, close: bool = False) -> None:
        """The talkback is over (`ws` given: only if it is still that person's)."""
        line = self.callers.get(call_id)
        if line is None or line.talk is None or (ws is not None and line.talk is not ws):
            return
        staff = line.talk
        line.talk = None
        await self._send_quiet(line.ws, json.dumps({"type": "talk", "on": False}))
        if close:
            try:
                await staff.close(code=4000)
            except Exception:
                pass

    async def _to_listeners(self, station_id: str, data: bytes) -> None:
        """Listeners get the show through the station's delay (straight away when it has none)."""
        air = self.airs.get(station_id)
        if air is None or air.passthrough():
            if air is not None:
                air.passed(data)
            await self._send_frame(station_id, data)
        else:
            air.feed(data)

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
        if status in ("on_air", "rejected", "completed", "abandoned"):
            # On air (or gone): a private word with them ends here, so nothing said
            # off the air can follow the caller onto it
            await self.talk_end(call_id, close=True)
        if not line.on_air:
            line.buf = np.zeros(0, dtype=np.int16)
            line.primed = False
        await self._send_quiet(line.ws, json.dumps({"type": "call_status", "status": status}))
        if status in ("rejected", "completed"):
            try:
                await line.ws.close(code=1000)
            except Exception:
                pass


async def _host_gone_now(station_id: str, show_id: str) -> None:
    from app.models.live_show import LiveShow, LiveShowStatus
    from app.models.station import Station
    from app.services.scheduler_engine import get_scheduler

    try:
        async for db in get_async_session():
            station = await db.get(Station, uuid.UUID(station_id))
            show = await db.get(LiveShow, uuid.UUID(show_id))
            if station is not None and show is not None and show.status == LiveShowStatus.LIVE \
                    and (station.automation_config or {}).get("live_show_id") == show_id:
                await get_scheduler()._host_gone(db, station, show, datetime.now(timezone.utc))
            await db.rollback()
            break
    except Exception:
        logger.warning("Could not look for a gone host on station %s", station_id, exc_info=True)


_holds: set = set()  # watchers that keep a show from cutting a timed item (kept from collection)

live_relay = StationLiveRelay()


@router.websocket("/ws/live/station/{station_id}/listen")
async def websocket_station_live_listen(websocket: WebSocket, station_id: str):
    """Public: a listener page hears the station's live show here while one is on air."""
    await websocket.accept()
    live_relay.listeners.setdefault(station_id, set()).add(websocket)
    try:
        await websocket.send_text(json.dumps(live_relay.state(station_id)))
        while True:
            text = await websocket.receive_text()  # also how the close is noticed
            # {"type":"audio","on":false}: this page hears the show on the station's
            # stream and wants only the live_state messages
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "audio":
                if msg.get("on") is False:
                    live_relay.no_audio.add(websocket)
                else:
                    live_relay.no_audio.discard(websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Live listen WS for station %s ended: %s", station_id, e)
    finally:
        live_relay.listeners.get(station_id, set()).discard(websocket)
        live_relay.no_audio.discard(websocket)


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

            if not user or not user.is_active or not token_is_current(payload, user):
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
        pass
    except Exception as e:
        logger.error("Live show WS error for show %s: %s", show_id, e)
    finally:
        # Also when the handler is cancelled (server shutdown, a client gone mid-send)
        live_show_manager.disconnect(show_id, websocket)


@router.websocket("/ws/live/{show_id}/audio")
async def websocket_live_audio(websocket: WebSocket, show_id: str):
    """Binary audio WebSocket — receives MP3 chunks from host browser, forwards to Icecast."""
    user = await _authenticate_ws(websocket, show_id)
    if user is None:
        return

    station_id = None
    started = False
    delay = dump = 0.0
    end_at = None
    taken_over = True
    record = False
    try:
        async for db in get_async_session():
            from app.models.station import Station
            from app.services.live_air import settings_for
            from app.services.live_show_service import get_show, start_if_scheduled
            show = await get_show(db, show_id)
            if show is not None:
                station_id = str(show.station_id)
                # Broadcasting puts the show on air: pause the station's automation
                # (with a broadcast delay, once the delay is full: see live_air.py)
                started = await start_if_scheduled(db, show.id, host_user_id=user.id)
                if started:
                    await db.commit()
                await db.refresh(show)
                station_row = await db.get(Station, show.station_id)
                if station_row is not None:
                    await db.refresh(station_row)
                    delay, dump, _rule = settings_for(station_row.automation_config)
                    from app.services import live_recording
                    record = live_recording.wanted(station_row.automation_config)
                    taken_over = (station_row.automation_config or {}).get("live_show_id") == str(show.id)
                if show.scheduled_end is not None:
                    end = show.scheduled_end
                    end_at = (end if end.tzinfo else end.replace(tzinfo=timezone.utc)).timestamp()
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
    logger.info("Audio WS authed for show %s user %s (station %s, delay %.0f s)", show_id, user.id, station_id, delay)
    if not taken_over:
        from app.services.live_show_service import take_over_when_heard
        live_relay.on_first_air[station_id] = take_over_when_heard
    await live_relay.set_on_air(station_id, show_id, delay=delay, dump=dump, end_at=end_at,
                                heard_now=taken_over, record=record)
    _air = live_relay.airs.get(station_id)
    if _air is not None and not taken_over and not _air.listening and delay > 0:
        from app.services.live_show_service import hold_for_timed_items
        _holds.add(_task := asyncio.ensure_future(hold_for_timed_items(station_id, _air)))
        _task.add_done_callback(_holds.discard)

    from app.config import settings
    try:
        while True:
            data = await websocket.receive_bytes()
            if live_relay.on_air.get(station_id) != show_id:
                # The show was ended while the host's page stayed open: the host was
                # heard for a minute over the returning music (Jerusalem 2026-11-17)
                continue
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
            # Shielded: a handler cancelled as the host leaves must still tell the
            # listener pages the show is off air (once the delay has played out), or
            # their music stays silenced
            await asyncio.shield(live_relay.host_left(station_id, show_id))


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
                if line.talk is not None and not line.on_air:
                    await live_relay._send_quiet(line.talk, data)   # off the air, to whoever talks to them
                else:
                    line.add(data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Caller WS %s ended: %s", call_id, e)
    finally:
        if line.talk is not None:
            staff, line.talk = line.talk, None
            try:
                await staff.close(code=4004)   # the caller is gone
            except Exception:
                pass
        if live_relay.callers.get(call_id) is line:
            live_relay.callers.pop(call_id, None)
            # Shielded: the grace timer must start even if this handler is cancelled
            await asyncio.shield(caller_left(call_id, line.show_id))


@router.websocket("/ws/live/{show_id}/talk/{call_id}")
async def websocket_talkback(websocket: WebSocket, show_id: str, call_id: str):
    """Talkback: the screener or the host talks to a web caller off the air. First
    message {"type":"auth","token":...} (same rule as the host's microphone), then
    their microphone as PCM frames, which go to that caller only; the caller's
    microphone comes back here. Nothing on this socket reaches listeners. Closes
    with 4004 when the caller is not connected or leaves, 4009 when the caller is on
    air, 4000 when the caller goes on air or someone else takes the talkback."""
    user = await _authenticate_ws(websocket, show_id)
    if user is None:
        return
    line = live_relay.callers.get(call_id)
    if line is None or line.show_id != show_id:
        await websocket.close(code=4004)
        return
    if line.on_air:
        await websocket.close(code=4009)
        return
    await live_relay.talk_start(call_id, websocket)
    logger.info("Talkback: user %s talks to caller %s (show %s)", user.id, call_id, show_id)
    try:
        await websocket.send_text(json.dumps({"type": "talk", "on": True, "sample_rate": LIVE_SAMPLE_RATE}))
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            current = live_relay.callers.get(call_id)
            if current is None or current.talk is not websocket:
                break
            if data:
                await live_relay._send_quiet(current.ws, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Talkback WS for call %s ended: %s", call_id, e)
    finally:
        await asyncio.shield(live_relay.talk_end(call_id, websocket))


# --- Broadcast helpers ---

async def broadcast_show_event(show_id: str, event_type: str, data: dict):
    """Broadcast an event to all WebSocket clients connected to a show."""
    await live_show_manager.broadcast_to_show(show_id, {
        "type": event_type,
        "data": data,
    })
