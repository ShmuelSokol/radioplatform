"""
Scheduling service — resolves what should play at any given time.
Handles time-based rules, recurrence, priority resolution, and now-playing state.
"""
import logging
import random
from datetime import datetime, time, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.now_playing import NowPlaying
from app.models.play_log import PlayLog
from app.models.playlist_entry import PlaybackMode, PlaylistEntry
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.schedule import Schedule
from app.models.schedule_block import DayOfWeek, RecurrenceType, ScheduleBlock, SunEvent

logger = logging.getLogger(__name__)


class SchedulingService:
    """Service for schedule resolution and now-playing state management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_block_for_station(
        self, station_id: UUID | str, at_time: datetime | None = None
    ) -> Optional[ScheduleBlock]:
        """
        Find the highest-priority active schedule block for a station at a given time.
        Returns None if no matching block found.
        """
        if at_time is None:
            at_time = datetime.utcnow()

        # Get station for sun calculations
        from app.models.station import Station
        stmt_station = select(Station).where(Station.id == station_id)
        station_result = await self.db.execute(stmt_station)
        station = station_result.scalar_one_or_none()

        # Get all active schedules for this station
        stmt = (
            select(Schedule)
            .where(Schedule.station_id == station_id, Schedule.is_active == True)
            .options(
                selectinload(Schedule.blocks).selectinload(ScheduleBlock.playlist_entries),
                selectinload(Schedule.blocks).selectinload(ScheduleBlock.playlist_template).selectinload(PlaylistTemplate.slots),
            )
            .order_by(Schedule.priority.desc())
        )
        result = await self.db.execute(stmt)
        schedules = result.scalars().all()

        # Find matching blocks
        matching_blocks = []
        for schedule in schedules:
            for block in schedule.blocks:
                if self._block_matches_time(block, at_time, station):
                    matching_blocks.append((schedule.priority, block.priority, block))

        if not matching_blocks:
            return None

        # Sort by schedule priority (desc), then block priority (desc)
        matching_blocks.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return matching_blocks[0][2]

    def _resolve_sun_time(self, sun_event: SunEvent, offset_minutes: int, station, at_date) -> time:
        """Resolve a sun-relative time to a concrete time value."""
        from app.services.sun_service import get_sun_times, offset_sun_time

        lat = station.latitude if station else None
        lon = station.longitude if station else None
        tz = station.timezone if station else "UTC"

        if lat is None or lon is None:
            # Default to Jerusalem if no coords
            lat, lon = 31.7683, 35.2137

        times = get_sun_times(lat, lon, tz, at_date)
        base_time = times.get(sun_event.value, times["sunset"]).time()
        return offset_sun_time(base_time, offset_minutes or 0)

    def _block_matches_time(
        self, block: ScheduleBlock, at_time: datetime, station=None
    ) -> bool:
        """Check if a schedule block is active at the given time."""
        current_time = at_time.time()
        current_weekday = at_time.strftime("%A").lower()
        current_day_of_month = at_time.day
        current_date = at_time.date()

        # Resolve start/end times (may be sun-relative)
        effective_start = block.start_time
        effective_end = block.end_time

        if block.start_sun_event and station:
            effective_start = self._resolve_sun_time(
                block.start_sun_event, block.start_sun_offset or 0, station, current_date
            )
        if block.end_sun_event and station:
            effective_end = self._resolve_sun_time(
                block.end_sun_event, block.end_sun_offset or 0, station, current_date
            )

        # Check time range (handle overnight blocks)
        if effective_start <= effective_end:
            if not (effective_start <= current_time < effective_end):
                return False
        else:
            if not (current_time >= effective_start or current_time < effective_end):
                return False

        # Check recurrence
        if block.recurrence_type == RecurrenceType.DAILY:
            return True
        elif block.recurrence_type == RecurrenceType.WEEKLY:
            if block.recurrence_pattern:
                return current_weekday in [d.lower() for d in block.recurrence_pattern]
            return True
        elif block.recurrence_type == RecurrenceType.MONTHLY:
            if block.recurrence_pattern:
                return current_day_of_month in block.recurrence_pattern
            return True
        elif block.recurrence_type == RecurrenceType.ONE_TIME:
            # Check date range if provided
            if block.start_date and block.end_date:
                return block.start_date <= current_date <= block.end_date
            elif block.start_date:
                return current_date == block.start_date
            # No dates specified — treat as always active (backwards compat)
            return True

        return False

    async def get_next_asset_for_block(
        self, block: ScheduleBlock, station_id: UUID | str | None = None
    ) -> Optional[UUID]:
        """
        Get the next asset ID to play from a block's playlist.
        If the block has a playlist_template_id, use template rotation instead.
        Respects playback_mode: sequential, shuffle, or weighted.
        """
        # Template rotation mode
        if block.playlist_template_id and block.playlist_template:
            return await self._resolve_template_asset(block, station_id)

        if not block.playlist_entries:
            return None

        enabled_entries = [e for e in block.playlist_entries if e.is_enabled]
        if not enabled_entries:
            return None

        mode = getattr(block, 'playback_mode', PlaybackMode.SEQUENTIAL)

        if mode == PlaybackMode.SHUFFLE:
            return await self._pick_shuffle(block, enabled_entries, station_id)
        elif mode == PlaybackMode.WEIGHTED:
            return await self._pick_weighted(block, enabled_entries)
        else:
            return await self._pick_sequential(block, enabled_entries, station_id)

    async def _pick_sequential(
        self, block: ScheduleBlock, entries: list[PlaylistEntry],
        station_id: UUID | str | None = None,
    ) -> Optional[UUID]:
        """Pick the next asset in position order, rotating through the list."""
        entries.sort(key=lambda e: e.position)

        # Find what was last played from this block
        last_played_id = await self._get_last_played_asset_for_block(block, station_id)

        if last_played_id is None:
            return entries[0].asset_id

        # Find the last played entry's position and pick the next one
        for i, entry in enumerate(entries):
            if str(entry.asset_id) == str(last_played_id):
                next_idx = (i + 1) % len(entries)
                return entries[next_idx].asset_id

        # Last played asset not in current entries — start from beginning
        return entries[0].asset_id

    async def _pick_shuffle(
        self, block: ScheduleBlock, entries: list[PlaylistEntry],
        station_id: UUID | str | None = None,
    ) -> Optional[UUID]:
        """Pick a random asset, avoiding recently played ones."""
        # Get recently played asset IDs for this block (last N plays)
        recent_ids = await self._get_recent_played_assets_for_block(
            block, station_id, limit=max(1, len(entries) // 2)
        )

        # Filter out recently played
        candidates = [e for e in entries if str(e.asset_id) not in recent_ids]

        # If all have been played recently, allow any entry
        if not candidates:
            candidates = entries

        chosen = random.choice(candidates)
        return chosen.asset_id

    async def _pick_weighted(
        self, block: ScheduleBlock, entries: list[PlaylistEntry]
    ) -> Optional[UUID]:
        """Pick an asset based on weight values (higher weight = more likely)."""
        weights = [max(e.weight, 1) for e in entries]
        chosen = random.choices(entries, weights=weights, k=1)[0]
        return chosen.asset_id

    async def _get_last_played_asset_for_block(
        self, block: ScheduleBlock, station_id: UUID | str | None = None
    ) -> Optional[str]:
        """Get the asset_id of the last thing played from this block's schedule."""
        if not station_id:
            station_id = block.schedule_id

        # Get asset IDs in this block
        block_asset_ids = [e.asset_id for e in block.playlist_entries if e.is_enabled]
        if not block_asset_ids:
            return None

        stmt = (
            select(PlayLog.asset_id)
            .where(
                PlayLog.station_id == station_id,
                PlayLog.asset_id.in_(block_asset_ids),
            )
            .order_by(desc(PlayLog.start_utc))
            .limit(1)
        )
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        return str(row) if row else None

    async def _get_recent_played_assets_for_block(
        self, block: ScheduleBlock, station_id: UUID | str | None = None, limit: int = 5
    ) -> set[str]:
        """Get recently played asset IDs from this block's playlist."""
        if not station_id:
            station_id = block.schedule_id

        block_asset_ids = [e.asset_id for e in block.playlist_entries if e.is_enabled]
        if not block_asset_ids:
            return set()

        stmt = (
            select(PlayLog.asset_id)
            .where(
                PlayLog.station_id == station_id,
                PlayLog.asset_id.in_(block_asset_ids),
            )
            .order_by(desc(PlayLog.start_utc))
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return {str(row) for row in result.scalars().all()}

    # ==================== Template Resolution ====================

    async def _resolve_template_asset(
        self, block: ScheduleBlock, station_id: UUID | str | None
    ) -> Optional[UUID]:
        """Resolve the next asset from a playlist template's rotation."""
        template = block.playlist_template
        slots = template.slots
        if not slots:
            return None

        # Check automation insertions first
        if station_id:
            insertion = await self._check_automation_insertions(station_id)
            if insertion:
                return insertion

        # Determine current slot by counting total plays for this station modulo slot count
        stmt = (
            select(func.count(PlayLog.id))
            .where(PlayLog.station_id == station_id)
        )
        result = await self.db.execute(stmt)
        total_plays = result.scalar() or 0

        slot_index = total_plays % len(slots)
        current_slot = slots[slot_index]

        return await self._pick_asset_for_slot(
            current_slot.asset_type, current_slot.category, station_id
        )

    async def _check_automation_insertions(
        self, station_id: UUID | str
    ) -> Optional[UUID]:
        """Check if an automation insertion (station ID jingle, time, weather) is due."""
        from app.models.station import Station

        stmt = select(Station).where(Station.id == station_id)
        result = await self.db.execute(stmt)
        station = result.scalar_one_or_none()
        if not station or not station.automation_config:
            return None

        config = station.automation_config
        now = datetime.utcnow()
        current_minute = now.minute

        # Hourly station ID jingle
        if config.get("hourly_station_id") and current_minute < 2:
            # Check if we already played an hourly_id this hour
            hour_start = now.replace(minute=0, second=0, microsecond=0)
            stmt = (
                select(func.count(PlayLog.id))
                .join(Asset, PlayLog.asset_id == Asset.id)
                .where(
                    PlayLog.station_id == station_id,
                    PlayLog.start_utc >= hour_start,
                    Asset.asset_type == "jingle",
                    Asset.category == "hourly_id",
                )
            )
            result = await self.db.execute(stmt)
            if (result.scalar() or 0) == 0:
                asset = await self._pick_asset_for_slot("jingle", "hourly_id", station_id)
                if asset:
                    return asset

        # Hourly time announcement
        if config.get("hourly_time_announcement") and current_minute < 2:
            hour_start = now.replace(minute=0, second=0, microsecond=0)
            stmt = (
                select(func.count(PlayLog.id))
                .join(Asset, PlayLog.asset_id == Asset.id)
                .where(
                    PlayLog.station_id == station_id,
                    PlayLog.start_utc >= hour_start,
                    Asset.asset_type == "jingle",
                    Asset.category == "time_announcement",
                )
            )
            result = await self.db.execute(stmt)
            if (result.scalar() or 0) == 0:
                from app.services.weather_spot_service import get_or_create_weather_spot_assets
                slot_key = now.strftime("%Y-%m-%dT%H:%M")[:14] + "00"
                time_asset, _ = await get_or_create_weather_spot_assets(self.db, slot_key)
                if time_asset:
                    return time_asset.id

        # Weather spot
        weather_interval = config.get("weather_interval_minutes", 30)
        if config.get("weather_enabled") and weather_interval > 0:
            if current_minute % weather_interval < 2:
                since = now - timedelta(minutes=weather_interval - 1)
                stmt = (
                    select(func.count(PlayLog.id))
                    .join(Asset, PlayLog.asset_id == Asset.id)
                    .where(
                        PlayLog.station_id == station_id,
                        PlayLog.start_utc >= since,
                        Asset.asset_type == "spot",
                        Asset.category == "weather_spot",
                    )
                )
                result = await self.db.execute(stmt)
                if (result.scalar() or 0) == 0:
                    from app.services.weather_spot_service import get_or_create_weather_spot_assets
                    slot_key = now.strftime("%Y-%m-%dT%H:%M")[:14] + "00"
                    _, weather_asset = await get_or_create_weather_spot_assets(self.db, slot_key)
                    if weather_asset:
                        return weather_asset.id

        return None

    async def _pick_asset_for_slot(
        self, asset_type: str, category: str | None, station_id: UUID | str | None
    ) -> Optional[UUID]:
        """Pick a random asset matching asset_type+category, avoiding recently played and do_not_play."""
        stmt = select(Asset).where(Asset.asset_type == asset_type)
        # Always exclude "do_not_play" assets
        stmt = stmt.where((Asset.category != "do_not_play") | (Asset.category.is_(None)))
        if category:
            stmt = stmt.where(Asset.category == category)
        result = await self.db.execute(stmt)
        candidates = list(result.scalars().all())

        if not candidates:
            return None

        # Exclude recently played (last 1/3 of candidates)
        if station_id and len(candidates) > 1:
            exclude_count = max(1, len(candidates) // 3)
            recent_stmt = (
                select(PlayLog.asset_id)
                .where(
                    PlayLog.station_id == station_id,
                    PlayLog.asset_id.in_([c.id for c in candidates]),
                )
                .order_by(desc(PlayLog.start_utc))
                .limit(exclude_count)
            )
            recent_result = await self.db.execute(recent_stmt)
            recent_ids = {str(r) for r in recent_result.scalars().all()}

            filtered = [c for c in candidates if str(c.id) not in recent_ids]
            if filtered:
                candidates = filtered

        chosen = random.choice(candidates)
        return chosen.id

    async def update_now_playing(
        self,
        station_id: UUID | str,
        asset_id: UUID | str | None,
        block_id: UUID | str | None = None,
        duration_seconds: float | None = None,
    ) -> NowPlaying:
        """
        Update or create the now-playing record for a station.
        """
        now = datetime.utcnow()
        ends_at = now + timedelta(seconds=duration_seconds) if duration_seconds else None

        # Check if record exists
        stmt = select(NowPlaying).where(NowPlaying.station_id == station_id)
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.asset_id = asset_id
            record.started_at = now
            record.ends_at = ends_at
            record.block_id = block_id
        else:
            record = NowPlaying(
                station_id=station_id,
                asset_id=asset_id,
                started_at=now,
                ends_at=ends_at,
                block_id=block_id,
            )
            self.db.add(record)

        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def get_now_playing(self, station_id: UUID | str) -> Optional[NowPlaying]:
        """Get current now-playing state for a station."""
        stmt = select(NowPlaying).where(NowPlaying.station_id == station_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def clear_now_playing(self, station_id: UUID | str) -> None:
        """Clear now-playing state (e.g., when station stops)."""
        stmt = select(NowPlaying).where(NowPlaying.station_id == station_id)
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            await self.db.delete(record)
            await self.db.commit()
