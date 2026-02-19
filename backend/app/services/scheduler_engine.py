"""
Scheduler engine — background task that automatically advances playback based on schedules.
This runs continuously and updates now-playing state when blocks change or assets end.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_session
from app.models.asset import Asset
from app.models.station import Station
from app.services.scheduling import SchedulingService

logger = logging.getLogger(__name__)


class SchedulerEngine:
    """
    Background scheduler that:
    1. Monitors active stations
    2. Resolves which block should be playing
    3. Advances to next asset when current one ends
    4. Broadcasts updates via WebSocket
    """
    
    def __init__(self, check_interval: int = 10):
        self.check_interval = check_interval  # seconds
        self.running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the scheduler engine."""
        if self.running:
            logger.warning("Scheduler already running")
            return
        
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Scheduler engine started")
    
    async def stop(self):
        """Stop the scheduler engine."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler engine stopped")
    
    async def _run_loop(self):
        """Main scheduler loop."""
        while self.running:
            try:
                async for db in get_async_session():
                    await self._check_all_stations(db)
                    break
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
            
            await asyncio.sleep(self.check_interval)
    
    async def _check_all_stations(self, db: AsyncSession):
        """Check all active stations and update their playback."""
        stmt = select(Station).where(Station.is_active == True)
        result = await db.execute(stmt)
        stations = result.scalars().all()
        
        for station in stations:
            try:
                await self._check_station(db, station)
            except Exception as e:
                logger.error(f"Error checking station {station.id}: {e}", exc_info=True)
    
    async def _check_station(self, db: AsyncSession, station: Station):
        """Check a single station and advance playback if needed."""
        service = SchedulingService(db)
        now = datetime.utcnow()
        
        # Get current now-playing state
        now_playing = await service.get_now_playing(station.id)
        
        # Check if current playback has ended
        needs_new_asset = False
        if now_playing:
            if now_playing.ends_at and now_playing.ends_at <= now:
                needs_new_asset = True
                logger.info(f"Station {station.id}: Current asset ended, need new one")
        else:
            # No playback active, start one
            needs_new_asset = True
            logger.info(f"Station {station.id}: No active playback, starting")
        
        if not needs_new_asset:
            return
        
        # Get the active block for current time
        block = await service.get_active_block_for_station(station.id, now)
        if not block:
            logger.warning(f"Station {station.id}: No active block found for current time")
            await service.clear_now_playing(station.id)
            return
        
        # Get next asset from block
        asset_id = await service.get_next_asset_for_block(block)
        if not asset_id:
            logger.warning(f"Station {station.id}: Block {block.id} has no assets")
            await service.clear_now_playing(station.id)
            return
        
        # Get asset duration
        stmt = select(Asset).where(Asset.id == asset_id)
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if not asset:
            logger.error(f"Asset {asset_id} not found")
            return
        
        duration = asset.duration_seconds or 180.0  # default 3 minutes if unknown
        
        # Update now-playing
        now_playing = await service.update_now_playing(
            station_id=station.id,
            asset_id=asset_id,
            block_id=block.id,
            duration_seconds=duration,
        )
        
        logger.info(
            f"Station {station.id}: Now playing asset {asset_id} "
            f"('{asset.title}') from block {block.id} ('{block.name}')"
        )
        
        # Broadcast update via WebSocket
        try:
            from app.api.v1.websocket import broadcast_now_playing_update
            await broadcast_now_playing_update(str(station.id), {
                "station_id": str(station.id),
                "asset_id": str(asset_id),
                "started_at": now_playing.started_at.isoformat(),
                "ends_at": now_playing.ends_at.isoformat() if now_playing.ends_at else None,
                "listener_count": now_playing.listener_count,
                "stream_url": now_playing.stream_url,
                "asset": {
                    "title": asset.title,
                    "artist": asset.artist,
                    "album": asset.album,
                    "album_art_path": asset.album_art_path,
                },
            })
        except Exception as e:
            logger.error(f"Failed to broadcast WebSocket update: {e}")


# Global scheduler instance
_scheduler_instance: Optional[SchedulerEngine] = None


def get_scheduler() -> SchedulerEngine:
    """Get or create the global scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = SchedulerEngine()
    return _scheduler_instance


async def start_scheduler():
    """Start the global scheduler."""
    scheduler = get_scheduler()
    await scheduler.start()


async def stop_scheduler():
    """Stop the global scheduler."""
    scheduler = get_scheduler()
    await scheduler.stop()
