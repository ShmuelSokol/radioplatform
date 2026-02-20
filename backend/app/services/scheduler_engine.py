"""
Scheduler engine — background task that automatically advances playback based on schedules.
This runs continuously and updates now-playing state when blocks change or assets end.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func as sql_func

from app.config import settings
from app.db.session import get_db
from app.models.asset import Asset
from app.models.channel_stream import ChannelStream
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog, PlaySource
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
        # Silence detection: station_id → datetime when silence first detected
        self._silence_start: dict[str, datetime] = {}
        # Block transition tracking: station_id → last active block ID
        self._last_block: dict[str, str | None] = {}
    
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
                try:
                    from app.services.alert_service import create_alert
                    await create_alert(
                        db,
                        alert_type="system",
                        severity="critical",
                        title=f"Station check failed: {station.name}",
                        message=str(e),
                        station_id=station.id,
                    )
                except Exception:
                    pass
    
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

    async def _check_live_show_hard_stop(self, db: AsyncSession, station: Station, live_show_id: str, now: datetime):
        """Check if a live show has reached its hard stop time."""
        from app.models.live_show import LiveShow, LiveShowStatus

        result = await db.execute(select(LiveShow).where(LiveShow.id == live_show_id))
        show = result.scalar_one_or_none()

        if not show or show.status != LiveShowStatus.LIVE:
            # Stale reference — clear it
            config = dict(station.automation_config or {})
            config.pop("live_show_id", None)
            station.automation_config = config
            logger.info("Cleared stale live_show_id from station %s", station.id)
            return

        if show.scheduled_end and show.scheduled_end <= now:
            # Hard stop
            from app.services.live_show_service import hard_stop_show
            await hard_stop_show(db, live_show_id)
            logger.warning("Hard stopped live show %s on station %s", live_show_id, station.id)

            # Broadcast WS event
            try:
                from app.api.v1.live_shows_ws import broadcast_show_event
                await broadcast_show_event(str(live_show_id), "show_hard_stopped", {
                    "show_id": str(live_show_id),
                })
            except Exception:
                pass

    async def _check_silence_detection(self, db: AsyncSession, station: Station, has_playing_asset: bool, is_blacked_out: bool):
        """
        Detect dead air (no audio playing) and trigger emergency fallback.

        If a station has no playing asset for longer than the configured threshold,
        a critical alert is raised and an emergency fallback asset is played.
        """
        station_key = str(station.id)
        now = datetime.utcnow()

        # If there IS a playing asset, clear the silence timer
        if has_playing_asset:
            self._silence_start.pop(station_key, None)
            return

        # If station is in blackout, don't treat as silence
        if is_blacked_out:
            self._silence_start.pop(station_key, None)
            return

        # Start or check silence timer
        if station_key not in self._silence_start:
            self._silence_start[station_key] = now
            return

        silence_started = self._silence_start[station_key]
        elapsed_seconds = (now - silence_started).total_seconds()

        # Get threshold from per-station config or global default
        auto_config = station.automation_config or {}
        threshold = auto_config.get("silence_threshold_seconds", settings.SILENCE_DETECTION_SECONDS)

        if elapsed_seconds < threshold:
            return

        # --- Silence threshold exceeded: dead air detected ---
        logger.critical(
            "Station %s (%s): Dead air detected — no audio for %.0f seconds",
            station.name, station.id, elapsed_seconds,
        )

        # Create critical alert
        try:
            from app.services.alert_service import create_alert
            await create_alert(
                db,
                alert_type="silence",
                severity="critical",
                title=f"Dead air detected: {station.name}",
                message=f"Station '{station.name}' has had no audio for {int(elapsed_seconds)}s",
                station_id=station.id,
            )
        except Exception as e:
            logger.error("Failed to create silence alert for station %s: %s", station.id, e)

        # Try emergency fallback: look for assets with category "emergency" or asset_type "jingle"
        emergency_category = settings.EMERGENCY_FALLBACK_CATEGORY
        try:
            stmt = select(Asset).where(
                (Asset.category == emergency_category) | (Asset.asset_type == "jingle")
            )
            result = await db.execute(stmt)
            fallback_assets = result.scalars().all()

            if fallback_assets:
                fallback = random.choice(fallback_assets)
                duration = fallback.duration or 60.0

                service = SchedulingService(db)
                await service.update_now_playing(
                    station_id=station.id,
                    asset_id=fallback.id,
                    block_id=None,
                    duration_seconds=duration,
                )

                # Reset silence timer since we started playing something
                self._silence_start.pop(station_key, None)

                logger.warning(
                    "Station %s: Emergency fallback activated — playing '%s' (id=%s)",
                    station.id, fallback.title, fallback.id,
                )

                # Broadcast emergency playback via WebSocket
                try:
                    from app.api.v1.websocket import broadcast_now_playing_update
                    await broadcast_now_playing_update(str(station.id), {
                        "station_id": str(station.id),
                        "asset_id": str(fallback.id),
                        "started_at": now.isoformat(),
                        "ends_at": (now + timedelta(seconds=duration)).isoformat(),
                        "emergency_fallback": True,
                        "asset": {
                            "title": fallback.title,
                            "artist": fallback.artist,
                            "album": fallback.album,
                            "album_art_path": fallback.album_art_path,
                        },
                    })
                except Exception as e:
                    logger.error("Failed to broadcast emergency fallback update: %s", e)
            else:
                logger.error(
                    "Station %s: No emergency fallback assets found (category='%s' or type='jingle')",
                    station.id, emergency_category,
                )
        except Exception as e:
            logger.error("Station %s: Emergency fallback failed: %s", station.id, e, exc_info=True)

    async def _check_block_transition(self, db: AsyncSession, station: Station, block) -> Asset | None:
        """
        Detect schedule block transitions and play an intro jingle if available.

        When the active block changes from the previous check, look for a jingle
        asset matching the new block name (e.g., category "morning_intro") and
        return it for playback before normal block content.
        """
        station_key = str(station.id)
        current_block_id = str(block.id) if block else None
        last_block_id = self._last_block.get(station_key)

        # Update tracking
        self._last_block[station_key] = current_block_id

        # No transition if same block or no new block
        if current_block_id is None or current_block_id == last_block_id:
            return None

        # First time seeing this station — don't trigger intro
        if last_block_id is None:
            return None

        # Block changed — look for intro jingle matching block name
        block_name = block.name.lower().replace(" ", "_") if block.name else ""
        intro_patterns = [
            f"{block_name}_intro",
            block_name,
        ]

        for pattern in intro_patterns:
            stmt = select(Asset).where(
                Asset.asset_type == "jingle",
                Asset.category == pattern,
            ).limit(1)
            result = await db.execute(stmt)
            jingle = result.scalar_one_or_none()
            if jingle:
                logger.info(
                    "Station %s: Block transition -> playing intro jingle '%s' for block '%s'",
                    station.id, jingle.title, block.name,
                )
                return jingle

        logger.debug(
            "Station %s: Block transition to '%s' but no matching intro jingle found",
            station.id, block.name,
        )
        return None

    async def _check_station(self, db: AsyncSession, station: Station):
        """Check a single station and advance playback if needed."""
        service = SchedulingService(db)
        now = datetime.utcnow()

        # Check if station is in live show mode — skip normal scheduler
        live_show_id = station.automation_config.get("live_show_id") if station.automation_config else None
        if live_show_id:
            await self._check_live_show_hard_stop(db, station, live_show_id, now)
            return  # Skip normal scheduler logic

        # Check blackout windows first
        is_blacked_out = await self._is_station_blacked_out(db, station, now)
        if is_blacked_out:
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
            # Run silence detection (will be a no-op during blackout)
            await self._check_silence_detection(db, station, has_playing_asset=bool(silence), is_blacked_out=True)
            return

        # Get current now-playing state
        now_playing = await service.get_now_playing(station.id)

        # Check if current playback has ended
        needs_new_asset = False
        if now_playing:
            if now_playing.ends_at and now_playing.ends_at <= now:
                needs_new_asset = True
                # Log the completed play
                if now_playing.asset_id:
                    play_log = PlayLog(
                        station_id=station.id,
                        asset_id=now_playing.asset_id,
                        start_utc=now_playing.started_at,
                        end_utc=now,
                        source=PlaySource.SCHEDULER,
                    )
                    db.add(play_log)
                    await db.flush()
                logger.info(f"Station {station.id}: Current asset ended, need new one")
        else:
            # No playback active, start one
            needs_new_asset = True
            logger.info(f"Station {station.id}: No active playback, starting")

        if not needs_new_asset:
            # Asset is still playing — clear silence timer and return
            await self._check_silence_detection(db, station, has_playing_asset=True, is_blacked_out=False)
            return

        # Get the active block for current time
        block = await service.get_active_block_for_station(station.id, now)
        if not block:
            logger.warning(f"Station {station.id}: No active block found for current time")
            try:
                from app.services.alert_service import create_alert
                await create_alert(
                    db, alert_type="playback_gap", severity="warning",
                    title=f"No active block: {station.name}",
                    message=f"No schedule block found for station '{station.name}' at {now.isoformat()}",
                    station_id=station.id,
                )
            except Exception:
                pass
            await service.clear_now_playing(station.id)
            # No block -> no playing asset -> check silence
            await self._check_silence_detection(db, station, has_playing_asset=False, is_blacked_out=False)
            return

        # Check for block transition — play intro jingle if available
        intro_jingle = await self._check_block_transition(db, station, block)
        if intro_jingle:
            duration = intro_jingle.duration or 10.0
            now_playing = await service.update_now_playing(
                station_id=station.id,
                asset_id=intro_jingle.id,
                block_id=block.id,
                duration_seconds=duration,
            )
            logger.info(
                f"Station {station.id}: Playing intro jingle '{intro_jingle.title}' "
                f"for block '{block.name}'"
            )
            try:
                from app.api.v1.websocket import broadcast_now_playing_update
                await broadcast_now_playing_update(str(station.id), {
                    "station_id": str(station.id),
                    "asset_id": str(intro_jingle.id),
                    "started_at": now_playing.started_at.isoformat(),
                    "ends_at": now_playing.ends_at.isoformat() if now_playing.ends_at else None,
                    "block_transition": True,
                    "asset": {
                        "title": intro_jingle.title,
                        "artist": intro_jingle.artist,
                        "album": intro_jingle.album,
                        "album_art_path": intro_jingle.album_art_path,
                    },
                })
            except Exception as e:
                logger.error(f"Failed to broadcast intro jingle update: {e}")
            # Jingle is now playing — silence cleared
            await self._check_silence_detection(db, station, has_playing_asset=True, is_blacked_out=False)
            return

        # Get next asset from block
        asset_id = await service.get_next_asset_for_block(block, station_id=station.id)
        if not asset_id:
            logger.warning(f"Station {station.id}: Block {block.id} has no assets")
            try:
                from app.services.alert_service import create_alert
                await create_alert(
                    db, alert_type="queue_empty", severity="warning",
                    title=f"Empty block: {block.name}",
                    message=f"Block '{block.name}' on station '{station.name}' has no assets to play",
                    station_id=station.id,
                    context={"block_id": str(block.id)},
                )
            except Exception:
                pass
            await service.clear_now_playing(station.id)
            # No asset available -> check silence
            await self._check_silence_detection(db, station, has_playing_asset=False, is_blacked_out=False)
            return

        # Get asset duration
        stmt = select(Asset).where(Asset.id == asset_id)
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if not asset:
            logger.error(f"Asset {asset_id} not found")
            try:
                from app.services.alert_service import create_alert
                await create_alert(
                    db, alert_type="asset_missing", severity="critical",
                    title=f"Asset not found: {asset_id}",
                    message=f"Asset {asset_id} referenced in block '{block.name}' does not exist",
                    station_id=station.id,
                    context={"asset_id": str(asset_id), "block_id": str(block.id)},
                )
            except Exception:
                pass
            # Asset missing -> check silence
            await self._check_silence_detection(db, station, has_playing_asset=False, is_blacked_out=False)
            return

        duration = asset.duration or 180.0  # default 3 minutes if unknown

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

        # Push metadata to Icecast so listeners see song info
        try:
            from app.services.icecast_service import update_icecast_metadata
            mount = station.broadcast_config.get("icecast_mount", settings.ICECAST_MOUNT) if station.broadcast_config else settings.ICECAST_MOUNT
            await update_icecast_metadata(mount, title=asset.title, artist=asset.artist)
        except Exception as e:
            logger.warning("Icecast metadata push failed: %s", e)

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

        # Asset started playing — clear silence timer
        await self._check_silence_detection(db, station, has_playing_asset=True, is_blacked_out=False)


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

        duration = asset.duration or 180.0

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
