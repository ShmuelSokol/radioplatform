"""
Icecast OTA broadcast control endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_db, require_admin
from app.models.station import Station

router = APIRouter(prefix="/icecast", tags=["icecast"])


@router.get("/status")
async def icecast_status(_=Depends(require_admin)):
    """Get Icecast configuration status."""
    from app.services.icecast_service import _icecast_clients
    return {
        "enabled": settings.icecast_enabled,
        "host": settings.ICECAST_HOST or None,
        "port": settings.ICECAST_PORT,
        "format": settings.ICECAST_FORMAT,
        "bitrate": settings.ICECAST_BITRATE,
        "active_streams": list(_icecast_clients.keys()),
    }


@router.post("/{station_id}/start")
async def start_stream(
    station_id: UUID,
    mount: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """Start Icecast OTA stream for a station."""
    if not settings.icecast_enabled:
        return {"error": "Icecast not configured. Set ICECAST_HOST environment variable."}

    stmt = select(Station).where(Station.id == station_id)
    result = await db.execute(stmt)
    station = result.scalar_one_or_none()
    if not station:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Station not found")

    from app.services.icecast_service import start_icecast_stream
    await start_icecast_stream(str(station_id), station.name, mount)
    return {"status": "started", "station": station.name, "mount": mount or f"/{station_id}"}


@router.post("/{station_id}/stop")
async def stop_stream(
    station_id: UUID,
    _=Depends(require_admin),
):
    """Stop Icecast OTA stream for a station."""
    from app.services.icecast_service import stop_icecast_stream
    await stop_icecast_stream(str(station_id))
    return {"status": "stopped"}
