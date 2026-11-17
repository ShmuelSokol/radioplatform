"""What the on-air self-check found lately (see app/services/air_watch.py)."""
from fastapi import APIRouter, Depends

from app.core.dependencies import require_staff
from app.models.user import User
from app.services.air_watch import get_watch

router = APIRouter(prefix="/air-watch", tags=["alerts"])


@router.get("")
async def air_watch_status(_: User = Depends(require_staff)):
    watch = get_watch()
    return {"last_run": watch.last_run, "runs": watch.runs, "findings": list(watch.recent)}
