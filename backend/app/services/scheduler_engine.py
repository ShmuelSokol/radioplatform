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

from app.db.session import get_db
from app.models.asset import Asset
from app.models.channel_stream import ChannelStream
from app.models.holiday_window import HolidayWindow
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
                async for db in get_db():
                    await self._check_all_stations(db)
                    break
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
            
            await asyncio.sleep(self.check_interval)
    
    async def _check_all_stations(self, db: AsyncSession):
        """Check all active stations and their channels."""
        stmt = select(Station).where(Station.is_active == True)
        result = await db.execute(stmt)
        stations = result.scalars().all()

        for station in stations:
            try:
                await self._check_station(db, station)
                # Also check per-channel playback
                ch_stmt = select(ChannelStream).where(
                    ChannelStream.station_id == station.id,
                    ChannelStream.is_active == True,
                    ChannelStream.schedule_id.isnot(None),
                )
                ch_result = await db.execute(ch_stmt)
                channels = ch_result.scalars().all()
                for channel in channels:
                    try:
                        await self._check_channel(db, station, channel)
                    except Exception as e:
                        logger.error(f"Error checking channel {channel.id}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Error checking station {station.id}: {e}", exc_info=True)
    
    async def _get_silence_asset(self, db: AsyncSession) -> Asset | None:
        """Get the silence asset for blackout playback, if one exists."""
        stmt = select(Asset).where(Asset.asset_type == "silence").limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _is_station_blacked_out(self, db: AsyncSession, station: Station, now: datetime) -> bool:
        """Check if a station is in a blackout window (Sabbath/holiday)."""
        stmt = select(HolidayWindow).where(
            HolidayWindow.is_blackout == True,
            HolidayWindow.start_datetime <= now,
            HolidayWindow.end_datetime > now,
        )
        result = await db.execute(stmt)
        windows = result.scalars().all()

        for window in windows:
            # Check if this window affects this station
            if window.affected_stations is None:
                # Null = affects all stations
                return True
            station_ids = window.affected_stations.get("station_ids", [])
            if str(station.id) in [str(sid) for sid in station_ids]:
                return True

        return False

    async def _check_station(self, db: AsyncSession, station: Station):
        """Check a single station and advance playback if needed."""
        service = SchedulingService(db)
        now = datetime.utcnow()

        # Check blackout windows first
        if await self._is_station_blacked_out(db, station, now):
            silence = await self._get_silence_asset(db)
            now_playing = await service.get_now_playing(station.id)

            if silence:
                # Play silence asset on loop during blackout
                needs_silence = False
                if not now_playing:
                    needs_silence = True
                elif now_playing.asset_id != silence.id:
                    needs_silence = True
                elif now_playing.ends_at and now_playing.ends_at <= now:
                    needs_silence = True  # Silence track ended, re-set it

                if needs_silence:
                    duration = silence.duration or 300.0
                    await service.update_now_playing(
                        station_id=station.id,
                        asset_id=silence.id,
                        block_id=None,
                        duration_seconds=duration,
                    )
                    logger.info(f"Station {station.id}: Blackout active, playing silence")
                    try:
                        from app.api.v1.websocket import broadcast_now_playing_update
                        await broadcast_now_playing_update(str(station.id), {
                            "station_id": str(station.id),
                            "asset_id": str(silence.id),
                            "started_at": now.isoformat(),
                            "ends_at": (now + timedelta(seconds=duration)).isoformat(),
                            "blackout": True,
                        })
                    except Exception as e:
                        logger.error(f"Failed to broadcast blackout update: {e}")
            else:
                # No silence asset — fall back to clearing playback
                if now_playing:
                    logger.info(f"Station {station.id}: Blackout active, clearing playback")
                    await service.clear_now_playing(station.id)
                    try:
                        from app.api.v1.websocket import broadcast_now_playing_update
                        await broadcast_now_playing_update(str(station.id), {
                            "station_id": str(station.id),
                            "asset_id": None,
                            "started_at": now.isoformat(),
                            "ends_at": None,
                            "blackout": True,
                        })
                    except Exception as e:
                        logger.error(f"Failed to broadcast blackout update: {e}")
            return

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
        asset_id = await service.get_next_asset_for_block(block, station_id=station.id)
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


    async def _check_channel(self, db: AsyncSession, station: Station, channel: ChannelStream):
        """Check a single channel within a station and advance its playback independently."""
        service = SchedulingService(db)
        now = datetime.utcnow()

        # Use a channel-specific key for now-playing (station_id + channel_id)
        # For now, channels with dedicated schedules run independently
        from app.models.now_playing import NowPlaying
        stmt = select(NowPlaying).where(
            NowPlaying.station_id == station.id,
            NowPlaying.channel_id == channel.id,
        )
        result = await db.execute(stmt)
        now_playing = result.scalar_one_or_none()

        needs_new = not now_playing or (now_playing.ends_at and now_playing.ends_at <= now)
        if not needs_new:
            return

        # Get active block from channel's dedicated schedule
        block = await service.get_active_block_for_station(station.id, now)
        if not block:
            return

        asset_id = await service.get_next_asset_for_block(block, station_id=station.id)
        if not asset_id:
            return

        stmt = select(Asset).where(Asset.id == asset_id)
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if not asset:
            return

        duration = asset.duration_seconds or 180.0

        if now_playing:
            now_playing.asset_id = asset_id
            now_playing.started_at = now
            now_playing.ends_at = now + timedelta(seconds=duration)
            now_playing.block_id = block.id
        else:
            now_playing = NowPlaying(
                station_id=station.id,
                channel_id=channel.id,
                asset_id=asset_id,
                started_at=now,
                ends_at=now + timedelta(seconds=duration),
                block_id=block.id,
            )
            db.add(now_playing)

        await db.commit()

        logger.info(
            f"Channel {channel.channel_name} ({channel.id}): Now playing '{asset.title}'"
        )


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
