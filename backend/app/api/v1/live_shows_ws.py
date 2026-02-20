"""
WebSocket endpoints for live show events and host audio streaming.
"""
import asyncio
import json
import logging
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

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


# --- WebSocket Endpoints ---

@router.websocket("/ws/live/{show_id}/events")
async def websocket_live_events(websocket: WebSocket, show_id: str, token: str = Query("")):
    """Real-time show state + caller updates WebSocket."""
    await live_show_manager.connect(show_id, websocket)

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
async def websocket_live_audio(websocket: WebSocket, show_id: str, token: str = Query("")):
    """Binary audio WebSocket — receives MP3 chunks from host browser, forwards to Icecast."""
    await audio_manager.connect(show_id, websocket)

    try:
        while True:
            data = await websocket.receive_bytes()
            # Forward to Icecast
            try:
                from app.services.icecast_service import get_icecast_client
                client = get_icecast_client(show_id)
                await client.push_audio(data)
            except Exception as e:
                logger.error("Failed to push audio to Icecast for show %s: %s", show_id, e)
    except WebSocketDisconnect:
        audio_manager.disconnect(show_id)
    except Exception as e:
        logger.error("Audio WS error for show %s: %s", show_id, e)
        audio_manager.disconnect(show_id)


# --- Broadcast helpers ---

async def broadcast_show_event(show_id: str, event_type: str, data: dict):
    """Broadcast an event to all WebSocket clients connected to a show."""
    await live_show_manager.broadcast_to_show(show_id, {
        "type": event_type,
        "data": data,
    })
