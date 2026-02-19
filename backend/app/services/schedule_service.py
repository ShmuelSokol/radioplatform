"""Scheduling service - manages schedule entries and real-time playback."""
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule_entry import ScheduleEntry
from app.models.play_log import PlayLog, PlaySource
from app.models.queue_entry import QueueEntry, QueueStatus
from app.schemas.schedule import ScheduleEntryCreate, ScheduleEntryUpdate


class ScheduleService:
    """Business logic for scheduling and playback tracking."""

    @staticmethod
    async def create_schedule_entry(
        db: AsyncSession, entry_data: ScheduleEntryCreate
    ) -> ScheduleEntry:
        """Create a new schedule entry."""
        entry = ScheduleEntry(
            station_id=uuid.UUID(str(entry_data.station_id)),
            start_time=entry_data.start_time.isoformat(),
            end_time=entry_data.end_time.isoformat(),
            content_type=entry_data.content_type,
            recurrence_rule=entry_data.recurrence_rule,
            priority=entry_data.priority,
            description=entry_data.description,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def get_schedule_entry(db: AsyncSession, entry_id: uuid.UUID) -> ScheduleEntry | None:
        """Get a schedule entry by ID."""
        result = await db.execute(select(ScheduleEntry).where(ScheduleEntry.id == entry_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_schedule_entries(
        db: AsyncSession, station_id: uuid.UUID | None = None, skip: int = 0, limit: int = 100
    ) -> Sequence[ScheduleEntry]:
        """List schedule entries, optionally filtered by station."""
        query = select(ScheduleEntry).offset(skip).limit(limit).order_by(ScheduleEntry.start_time)
        if station_id:
            query = query.where(ScheduleEntry.station_id == station_id)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update_schedule_entry(
        db: AsyncSession, entry_id: uuid.UUID, update_data: ScheduleEntryUpdate
    ) -> ScheduleEntry | None:
        """Update a schedule entry."""
        entry = await ScheduleService.get_schedule_entry(db, entry_id)
        if not entry:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            if field in ("start_time", "end_time") and value:
                value = value.isoformat()
            setattr(entry, field, value)

        await db.commit()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def delete_schedule_entry(db: AsyncSession, entry_id: uuid.UUID) -> bool:
        """Delete a schedule entry."""
        entry = await ScheduleService.get_schedule_entry(db, entry_id)
        if not entry:
            return False
        await db.delete(entry)
        await db.commit()
        return True

    @staticmethod
    async def get_current_schedule(
        db: AsyncSession, station_id: uuid.UUID, at_time: datetime | None = None
    ) -> ScheduleEntry | None:
        """Get the active schedule entry for a station at a specific time."""
        if not at_time:
            at_time = datetime.now(timezone.utc)

        at_time_str = at_time.isoformat()
        query = (
            select(ScheduleEntry)
            .where(
                ScheduleEntry.station_id == station_id,
                ScheduleEntry.start_time <= at_time_str,
                ScheduleEntry.end_time >= at_time_str,
            )
            .order_by(ScheduleEntry.priority.desc())
        )
        result = await db.execute(query)
        return result.scalars().first()

    @staticmethod
    async def log_play(
        db: AsyncSession,
        station_id: uuid.UUID,
        asset_id: uuid.UUID | None,
        source: PlaySource = PlaySource.SCHEDULER,
    ) -> PlayLog:
        """Log when an asset starts playing."""
        log = PlayLog(
            station_id=station_id,
            asset_id=asset_id,
            start_utc=datetime.now(timezone.utc),
            source=source,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    @staticmethod
    async def end_play(db: AsyncSession, log_id: uuid.UUID) -> PlayLog | None:
        """Mark a play log as ended."""
        result = await db.execute(select(PlayLog).where(PlayLog.id == log_id))
        log = result.scalar_one_or_none()
        if log:
            log.end_utc = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(log)
        return log

    @staticmethod
    async def get_now_playing(db: AsyncSession, station_id: uuid.UUID) -> PlayLog | None:
        """Get the current playing asset for a station (most recent log without end_utc)."""
        query = (
            select(PlayLog)
            .where(PlayLog.station_id == station_id, PlayLog.end_utc.is_(None))
            .order_by(PlayLog.start_utc.desc())
        )
        result = await db.execute(query)
        return result.scalars().first()
