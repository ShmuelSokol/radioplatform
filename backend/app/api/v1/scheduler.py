"""
Scheduler control endpoints — start/stop the automatic scheduling engine.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import require_admin_or_manager
from app.models.user import User
from app.services.scheduler_engine import get_scheduler

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.post("/start")
async def start_scheduler(current_user: User = Depends(require_admin_or_manager)):
    """Start the automatic scheduling engine."""
    scheduler = get_scheduler()
    if scheduler.running:
        return {"status": "already_running", "message": "Scheduler is already active"}
    
    await scheduler.start()
    return {"status": "started", "message": "Scheduler engine started"}


@router.post("/stop")
async def stop_scheduler(current_user: User = Depends(require_admin_or_manager)):
    """Stop the automatic scheduling engine."""
    scheduler = get_scheduler()
    if not scheduler.running:
        return {"status": "already_stopped", "message": "Scheduler is not running"}
    
    await scheduler.stop()
    return {"status": "stopped", "message": "Scheduler engine stopped"}


@router.get("/status")
async def get_scheduler_status():
    """Get current scheduler status."""
    scheduler = get_scheduler()
    return {
        "running": scheduler.running,
        "check_interval": scheduler.check_interval,
    }
