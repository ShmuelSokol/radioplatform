"""Now-playing WebSocket endpoint."""
import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.schedule_service import ScheduleService
from app.schemas.schedule import NowPlayingResponse

router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    """Manages WebSocket connections for now-playing updates."""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, station_id: str, websocket: WebSocket):
        await websocket.accept()
        if station_id not in self.active_connections:
            self.active_connections[station_id] = []
        self.active_connections[station_id].append(websocket)

    def disconnect(self, station_id: str, websocket: WebSocket):
        if station_id in self.active_connections:
            self.active_connections[station_id].remove(websocket)
            if not self.active_connections[station_id]:
                del self.active_connections[station_id]

    async def broadcast(self, station_id: str, message: dict):
        """Broadcast a message to all clients listening to a station."""
        if station_id not in self.active_connections:
            return
        dead_connections = []
        for connection in self.active_connections[station_id]:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        # Clean up dead connections
        for conn in dead_connections:
            self.disconnect(station_id, conn)


manager = ConnectionManager()


@router.websocket("/nowplaying/{station_id}")
async def nowplaying_websocket(websocket: WebSocket, station_id: str):
    """
    WebSocket endpoint for real-time now-playing updates.
    Sends updates every 2 seconds with current playback state.
    """
    await manager.connect(station_id, websocket)
    
    try:
        # Send initial state immediately
        async for db in get_db():
            try:
                station_uuid = uuid.UUID(station_id)
                play_log = await ScheduleService.get_now_playing(db, station_uuid)
                
                response = NowPlayingResponse(
                    station_id=station_id,
                    asset_id=str(play_log.asset_id) if play_log and play_log.asset_id else None,
                    asset_title=play_log.asset.title if play_log and play_log.asset else None,
                    started_at=play_log.start_utc if play_log else None,
                    duration_seconds=play_log.asset.duration_seconds if play_log and play_log.asset else None,
                    elapsed_seconds=(
                        (datetime.now(timezone.utc) - play_log.start_utc).total_seconds()
                        if play_log and play_log.start_utc
                        else None
                    ),
                    state="playing" if play_log else "stopped",
                )
                await websocket.send_json(response.model_dump(mode="json"))
            finally:
                break
        
        # Keep connection alive and send periodic updates
        while True:
            await asyncio.sleep(2)
            
            async for db in get_db():
                try:
                    station_uuid = uuid.UUID(station_id)
                    play_log = await ScheduleService.get_now_playing(db, station_uuid)
                    
                    response = NowPlayingResponse(
                        station_id=station_id,
                        asset_id=str(play_log.asset_id) if play_log and play_log.asset_id else None,
                        asset_title=play_log.asset.title if play_log and play_log.asset else None,
                        started_at=play_log.start_utc if play_log else None,
                        duration_seconds=play_log.asset.duration_seconds if play_log and play_log.asset else None,
                        elapsed_seconds=(
                            (datetime.now(timezone.utc) - play_log.start_utc).total_seconds()
                            if play_log and play_log.start_utc
                            else None
                        ),
                        state="playing" if play_log else "stopped",
                    )
                    await websocket.send_json(response.model_dump(mode="json"))
                finally:
                    break
                    
    except WebSocketDisconnect:
        manager.disconnect(station_id, websocket)
    except Exception as e:
        print(f"WebSocket error for station {station_id}: {e}")
        manager.disconnect(station_id, websocket)
