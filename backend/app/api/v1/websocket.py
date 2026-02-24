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

from app.config import settings
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
        for ws in self.connections:
            try:
                await ws.send_text(message_str)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.connections.discard(ws)

alert_manager = AlertConnectionManager()


@router.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    """WebSocket endpoint for real-time alert notifications to admin clients."""
    await alert_manager.connect(websocket)
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
    await manager.connect(station_id, websocket)
    
    try:
        # Send initial state
        from app.db.session import get_async_session
        async for db in get_async_session():
            service = SchedulingService(db)
            now_playing = await service.get_now_playing(station_id)
            
            if now_playing:
                asset = now_playing.asset
                # Get audio analysis data
                analysis = {}
                audio_url = None
                if asset:
                    if asset.metadata_extra:
                        analysis = asset.metadata_extra.get("audio_analysis", {})
                    # Build audio URL
                    from app.config import settings
                    file_path = asset.file_path
                    if file_path.startswith("http://") or file_path.startswith("https://"):
                        audio_url = file_path
                    elif settings.supabase_storage_enabled:
                        bucket = settings.SUPABASE_STORAGE_BUCKET
                        audio_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_path}"

                duration = (asset.duration if asset else None) or 180.0

                # Peek at next pending queue entry
                from sqlalchemy import select as sa_select, or_
                from app.models.queue_entry import QueueEntry
                from datetime import datetime, timezone
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
                if next_entry and next_entry.asset:
                    na = next_entry.asset
                    na_analysis = {}
                    if na.metadata_extra:
                        na_analysis = na.metadata_extra.get("audio_analysis", {})
                    na_file_path = na.file_path
                    na_audio_url = None
                    if na_file_path.startswith("http://") or na_file_path.startswith("https://"):
                        na_audio_url = na_file_path
                    elif settings.supabase_storage_enabled:
                        na_audio_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{na_file_path}"
                    next_asset_data = {
                        "id": str(na.id),
                        "title": na.title,
                        "artist": na.artist,
                        "audio_url": na_audio_url,
                        "cue_in": na_analysis.get("cue_in_seconds", 0),
                        "replay_gain_db": na_analysis.get("replay_gain_db", 0),
                    }

                await websocket.send_json({
                    "type": "now_playing",
                    "data": {
                        "station_id": str(now_playing.station_id),
                        "asset_id": str(now_playing.asset_id) if now_playing.asset_id else None,
                        "started_at": now_playing.started_at.isoformat(),
                        "ends_at": now_playing.ends_at.isoformat() if now_playing.ends_at else None,
                        "listener_count": now_playing.listener_count,
                        "stream_url": now_playing.stream_url,
                        "hls_url": f"/hls/{station_id}/stream.m3u8" if settings.liquidsoap_enabled else None,
                        "asset": {
                            "title": asset.title if asset else "Unknown",
                            "artist": asset.artist if asset else None,
                            "album": asset.album if asset else None,
                            "album_art_path": asset.album_art_path if asset else None,
                            "audio_url": audio_url,
                            "cue_in": analysis.get("cue_in_seconds", 0),
                            "cue_out": analysis.get("cue_out_seconds", duration),
                            "cross_start": analysis.get("cross_start_seconds", duration - 3.0),
                            "replay_gain_db": analysis.get("replay_gain_db", 0),
                        } if asset else None,
                        "next_asset": next_asset_data,
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
