"""
WebSocket endpoints for real-time updates.
"""
import asyncio
import json
import logging
from typing import Dict, Set
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.services.scheduling import SchedulingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

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
        
        dead_connections = set()
        message_str = json.dumps(message)
        
        for connection in self.active_connections[station_id]:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logger.error(f"Failed to send to WebSocket: {e}")
                dead_connections.add(connection)
        
        # Clean up dead connections
        for connection in dead_connections:
            self.disconnect(station_id, connection)

manager = ConnectionManager()


@router.websocket("/ws/now-playing/{station_id}")
async def websocket_now_playing(websocket: WebSocket, station_id: str):
    """
    WebSocket endpoint for real-time now-playing updates.
    Clients subscribe to a station and receive updates when playback changes.
    """
    await manager.connect(station_id, websocket)
    
    try:
        # Send initial state
        from app.db.session import get_async_session
        async for db in get_async_session():
            service = SchedulingService(db)
            now_playing = await service.get_now_playing(station_id)
            
            if now_playing:
                await websocket.send_json({
                    "type": "now_playing",
                    "data": {
                        "station_id": str(now_playing.station_id),
                        "asset_id": str(now_playing.asset_id) if now_playing.asset_id else None,
                        "started_at": now_playing.started_at.isoformat(),
                        "ends_at": now_playing.ends_at.isoformat() if now_playing.ends_at else None,
                        "listener_count": now_playing.listener_count,
                        "stream_url": now_playing.stream_url,
                        "asset": {
                            "title": now_playing.asset.title if now_playing.asset else "Unknown",
                            "artist": now_playing.asset.artist if now_playing.asset else None,
                            "album": now_playing.asset.album if now_playing.asset else None,
                            "album_art_path": now_playing.asset.album_art_path if now_playing.asset else None,
                        } if now_playing.asset else None,
                    }
                })
            break
        
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


async def broadcast_now_playing_update(station_id: str, now_playing_data: dict):
    """
    Helper function to broadcast now-playing updates to all connected clients.
    Called by the scheduling engine when playback changes.
    """
    await manager.broadcast_to_station(station_id, {
        "type": "now_playing",
        "data": now_playing_data,
    })
