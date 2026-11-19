"""
The old "FM feed" (backend-run Icecast source), retired 2026-11-19.

The backend used to open an Icecast source itself and push the live show host's
microphone to it. It carried no music, no callers' mix and no automation, and the
source dropped between shows (checklist V2-116). The FM feed is the station's stream
from the playout engine, which carries everything the station plays at all times,
live shows included (wiki/streaming-architecture.md, wiki/local-fm-box.md).

The routes stay so an old bookmark or script gets a clear answer instead of a 404.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.dependencies import require_admin

router = APIRouter(prefix="/icecast", tags=["icecast"])

GONE = ("The backend's own FM feed is retired. The FM feed is the station's stream from "
        "the playout engine, which carries the whole station (music, live shows, callers).")


@router.get("/status")
async def icecast_status(_=Depends(require_admin)):
    """Where the FM feed comes from now, per station."""
    return {
        "enabled": False,
        "retired": True,
        "detail": GONE,
        "active_streams": [],
        "engine_streams": {sid: settings.engine_stream_url(sid) for sid in sorted(settings.engine_station_ids) if settings.playout_engine_enabled},
    }


@router.post("/{station_id}/start")
async def start_stream(station_id: UUID, _=Depends(require_admin)):
    raise HTTPException(status_code=410, detail=GONE)


@router.post("/{station_id}/stop")
async def stop_stream(station_id: UUID, _=Depends(require_admin)):
    raise HTTPException(status_code=410, detail=GONE)
