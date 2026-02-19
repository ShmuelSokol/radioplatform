"""
Scheduling service — resolves what should play at any given time.
Handles time-based rules, recurrence, priority resolution, and now-playing state.
"""
import logging
from datetime import datetime, time, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.now_playing import NowPlaying
from app.models.playlist_entry import PlaylistEntry
from app.models.schedule import Schedule
from app.models.schedule_block import DayOfWeek, RecurrenceType, ScheduleBlock

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

        # Get all active schedules for this station
        stmt = (
            select(Schedule)
            .where(Schedule.station_id == station_id, Schedule.is_active == True)
            .options(selectinload(Schedule.blocks).selectinload(ScheduleBlock.playlist_entries))
            .order_by(Schedule.priority.desc())
        )
        result = await self.db.execute(stmt)
        schedules = result.scalars().all()

        # Find matching blocks
        matching_blocks = []
        for schedule in schedules:
            for block in schedule.blocks:
                if self._block_matches_time(block, at_time):
                    matching_blocks.append((schedule.priority, block.priority, block))

        if not matching_blocks:
            return None

        # Sort by schedule priority (desc), then block priority (desc)
        matching_blocks.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return matching_blocks[0][2]

    def _block_matches_time(self, block: ScheduleBlock, at_time: datetime) -> bool:
        """Check if a schedule block is active at the given time."""
        current_time = at_time.time()
        current_weekday = at_time.strftime("%A").lower()  # "monday", "tuesday", etc.
        current_day_of_month = at_time.day

        # Check time range (handle overnight blocks)
        if block.start_time <= block.end_time:
            # Normal range (e.g., 08:00 - 17:00)
            if not (block.start_time <= current_time < block.end_time):
                return False
        else:
            # Overnight range (e.g., 22:00 - 02:00)
            if not (current_time >= block.start_time or current_time < block.end_time):
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
            # For one-time events, would need start_date/end_date fields
            # For now, treat as daily
            return True

        return False

    async def get_next_asset_for_block(self, block: ScheduleBlock) -> Optional[UUID]:
        """
        Get the next asset ID to play from a block's playlist.
        For now, returns the first enabled entry (sequential mode).
        TODO: Implement shuffle, weighted random, rotation tracking.
        """
        if not block.playlist_entries:
            return None

        enabled_entries = [e for e in block.playlist_entries if e.is_enabled]
        if not enabled_entries:
            return None

        # Sort by position
        enabled_entries.sort(key=lambda e: e.position)
        return enabled_entries[0].asset_id

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
