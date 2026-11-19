"""
Scheduling service — resolves what should play at any given time.
Handles time-based rules, recurrence, priority resolution, and now-playing state.
"""

import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import (
    Asset, fit_for_random_pick, in_category, not_contract_audio, not_holiday_only, not_placeholder,
)
from app.models.now_playing import NowPlaying
from app.models.play_log import PlayLog
from app.models.playlist_entry import PlaybackMode, PlaylistEntry
from app.models.playlist_template import PlaylistTemplate
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock, SunEvent

logger = logging.getLogger(__name__)


def block_in_date_range(block, day) -> bool:
    """Is `day` (a station-local date) inside the block's start_date..end_date?

    Either end may be empty (open-ended). The one place the date range of a
    schedule block is checked: the resolver (and so playout, the guide, the
    look-ahead and the queue refresh) goes through SchedulingService._recurs_on.
    """
    start = getattr(block, "start_date", None)
    end = getattr(block, "end_date", None)
    if start is not None and day < start:
        return False
    if end is not None and day > end:
        return False
    return True


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
            at_time = datetime.now(timezone.utc)

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
                selectinload(Schedule.blocks)
                .selectinload(ScheduleBlock.playlist_template)
                .selectinload(PlaylistTemplate.slots),
            )
            .order_by(Schedule.priority.desc())
        )
        result = await self.db.execute(stmt)
        schedules = result.scalars().all()

        ranked = self.rank_matching_blocks(schedules, at_time, station)
        return ranked[0][1] if ranked else None

    @staticmethod
    def station_local(at_time: datetime, station) -> datetime:
        """Block times are station wall-clock times, so match them in the station's zone."""
        from zoneinfo import ZoneInfo

        if at_time.tzinfo is None:
            at_time = at_time.replace(tzinfo=timezone.utc)
        try:
            return at_time.astimezone(ZoneInfo((station.timezone if station else None) or "UTC"))
        except Exception:
            return at_time.astimezone(timezone.utc)

    @staticmethod
    def block_rank(schedule: Schedule, block: ScheduleBlock) -> tuple:
        """Sort key for which block wins when several cover the same moment (highest wins).

        Priority decides first. Equal priorities are settled the same way every
        time: the more specific block wins (one-time over monthly over weekly
        over daily), then the shorter one, then the newer one.
        """
        specificity = {
            RecurrenceType.ONE_TIME: 3,
            RecurrenceType.MONTHLY: 2,
            RecurrenceType.WEEKLY: 1,
            RecurrenceType.DAILY: 0,
        }.get(block.recurrence_type, 0)
        if getattr(block, "until_next", False):
            # The running playlist is the base layer: a timed block over it wins a tie.
            specificity = -1
        length = 0
        if block.start_time is not None and block.end_time is not None:
            start = block.start_time.hour * 60 + block.start_time.minute
            end = block.end_time.hour * 60 + block.end_time.minute
            length = (end - start) % (24 * 60) or 24 * 60
        return (
            schedule.priority or 0,
            block.priority or 0,
            specificity,
            -length,
            str(block.created_at or ""),
            str(block.id),
        )

    def rank_matching_blocks(
        self, schedules, at_time: datetime, station=None
    ) -> list[tuple[Schedule, ScheduleBlock]]:
        """Every block active at at_time, winner first."""
        local = self.station_local(at_time, station)
        matching = [
            (schedule, block)
            for schedule in schedules
            for block in schedule.blocks
            if not getattr(block, "until_next", False)
            and self._block_matches_time(block, local, station)
        ]
        running = self.running_playlist(schedules, local, station)
        if running is not None:
            matching.append(running)
        matching.sort(key=lambda sb: self.block_rank(*sb), reverse=True)
        return matching

    def running_playlist(self, schedules, local: datetime, station=None):
        """The start-only block that started most recently, or None.

        Start-only blocks (until_next) work like playlists in the studio
        program: each has a start and stays on until another one starts.
        """
        best = None
        for schedule in schedules:
            for block in schedule.blocks:
                if not getattr(block, "until_next", False):
                    continue
                started = self.last_start(block, local, station)
                if started is None:
                    continue
                key = (started, self.block_rank(schedule, block))
                if best is None or key > best[0]:
                    best = (key, (schedule, block))
        return best[1] if best else None

    def last_start(self, block: ScheduleBlock, local: datetime, station=None) -> datetime | None:
        """Most recent moment (station time, naive) this block started, or None.

        How far back depends on the recurrence: daily and weekly blocks look back
        8 days, a monthly block back to its previous day of the month (up to about
        two months, for a 31st in a short month), and a one-time block with a date
        range holds from its first start until the range ends.

        Only starts after the block was made count: a block saved at 6:40 to start
        at 6:52 has not started yet, even though yesterday's 6:52 is in the past
        (it used to take over the moment it was saved).
        """
        now = local.replace(tzinfo=None)
        if (
            block.end_date
            and (block.recurrence_type != RecurrenceType.ONE_TIME or block.start_date)
            and now >= datetime.combine(block.end_date + timedelta(days=1), time(0))
        ):
            return None  # the date range is over
        made = getattr(block, "created_at", None)
        if made is not None:
            if made.tzinfo is None:
                made = made.replace(tzinfo=timezone.utc)
            made = made.astimezone(local.tzinfo).replace(tzinfo=None) if local.tzinfo else made.replace(tzinfo=None)
        for day in self._start_days(block, now.date()):
            started = self._block_start(block, day, station)
            if started > now:
                continue
            if made is not None and started < made:
                return None
            return started
        return None

    @classmethod
    def _start_days(cls, block: ScheduleBlock, today):
        """Days this block may have started on, newest first, from tomorrow back.

        Tomorrow is included because a start with a sun offset can fall on the
        neighbouring calendar day (sunset + 90 min after midnight). Monthly days
        are worked out directly instead of walking back day by day: this runs for
        every song the playlist generator picks."""
        tomorrow = today + timedelta(days=1)
        pattern = block.recurrence_pattern
        if block.recurrence_type == RecurrenceType.MONTHLY and pattern:
            wanted = sorted({d for d in pattern if isinstance(d, int) and 1 <= d <= 31}, reverse=True)
            year, month = tomorrow.year, tomorrow.month
            # This month and the two before: a 31st can be two months back (March -> January).
            for _ in range(3):
                for d in wanted:
                    try:
                        day = date(year, month, d)
                    except ValueError:
                        continue  # no 31st this month
                    if day <= tomorrow:
                        yield day
                year, month = (year, month - 1) if month > 1 else (year - 1, 12)
            return
        if block.recurrence_type == RecurrenceType.ONE_TIME and block.start_date and block.end_date:
            # Every day of the range is a start (last_start stops it once the range
            # is over), so the newest start is at most a day or two back.
            day = min(tomorrow, block.end_date)
            while day >= block.start_date:
                yield day
                day -= timedelta(days=1)
            return
        for back in range(-1, 9):
            day = today - timedelta(days=back)
            if cls._recurs_on(block, day):
                yield day

    @staticmethod
    def _recurs_on(block: ScheduleBlock, day) -> bool:
        """Does this block's recurrence include the given date (station-local)?

        A daily, weekly or monthly block with a start_date and/or end_date only
        recurs on dates inside that range (either may be empty = open-ended).
        """
        if block.recurrence_type != RecurrenceType.ONE_TIME and not block_in_date_range(block, day):
            return False
        if block.recurrence_type == RecurrenceType.WEEKLY:
            if block.recurrence_pattern:
                return day.strftime("%A").lower() in [d.lower() for d in block.recurrence_pattern]
            return True
        if block.recurrence_type == RecurrenceType.MONTHLY:
            if block.recurrence_pattern:
                return day.day in block.recurrence_pattern
            return True
        if block.recurrence_type == RecurrenceType.ONE_TIME:
            if block.start_date and block.end_date:
                return block.start_date <= day <= block.end_date
            if block.start_date:
                return day == block.start_date
            return True
        return True

    # Stations in Israel without saved coordinates use Jerusalem's sun times.
    ISRAEL_ZONES = ("Asia/Jerusalem", "Asia/Tel_Aviv")
    JERUSALEM = (31.7683, 35.2137)
    # A station with no coordinates keeps its zmanim where its Shabbos silence is worked out
    # (Lakewood for New York time, tests/sim/checks.PLACES): a "sunset + 72 min" Motzei
    # Shabbos block used the block's clock time instead (2026-11-19; the live stations have
    # no coordinates stored).
    ZONE_PLACES = {"America/New_York": (40.0821, -74.2097)}

    def _sun_moment(
        self, sun_event: SunEvent, offset_minutes: int, station, day, fallback: time | None = None
    ) -> datetime:
        """When a sun-relative time falls for `day`, as a naive station-time datetime.

        The offset is added to the full date and time, so "sunset + 90 min" after a
        23:00 sunset is 00:30 the NEXT day (it used to wrap to 00:30 of the same day).

        Falls back to the block's clock time (`fallback`) where the sun does not rise
        or set that day (polar summer or winter), and where the station has no
        coordinates and is not in Israel (it used to get Jerusalem's sunset, so a New
        York station's sunset block started in the morning)."""
        from app.services.sun_service import get_sun_times

        lat = station.latitude if station else None
        lon = station.longitude if station else None
        tz = (station.timezone if station else None) or "UTC"

        if (lat is None or lon is None) and tz in self.ZONE_PLACES:
            lat, lon = self.ZONE_PLACES[tz]
        if lat is None or lon is None:
            if tz in self.ISRAEL_ZONES or fallback is None:
                lat, lon = self.JERUSALEM
            else:
                logger.warning("Station %s has no latitude/longitude: %s block uses its clock time %s",
                               getattr(station, "name", None), sun_event.value, fallback)
                return datetime.combine(day, fallback)

        try:
            times = get_sun_times(lat, lon, tz, day)
        except ValueError as exc:
            if fallback is None:
                raise
            logger.warning("No %s at %s, %s on %s (%s): using the block's clock time %s",
                           sun_event.value, lat, lon, day, exc, fallback)
            return datetime.combine(day, fallback)
        base = times.get(sun_event.value, times["sunset"]).replace(tzinfo=None)
        noon = times["noon"].replace(tzinfo=None)
        # Far north astral can give an evening time from the night before (dusk at
        # 00:27 on the same date) or a morning time from the next night; keep the
        # evening events after this day's noon and the morning events before it.
        if sun_event in (SunEvent.SUNSET, SunEvent.DUSK) and base < noon:
            base += timedelta(days=1)
        elif sun_event in (SunEvent.SUNRISE, SunEvent.DAWN) and base > noon:
            base -= timedelta(days=1)
        return base + timedelta(minutes=offset_minutes or 0)

    def _resolve_sun_time(
        self, sun_event: SunEvent, offset_minutes: int, station, at_date, fallback: time | None = None
    ) -> time:
        """The clock time of a sun-relative time (see _sun_moment)."""
        return self._sun_moment(sun_event, offset_minutes, station, at_date, fallback).time()

    def _block_start(self, block: ScheduleBlock, day, station=None) -> datetime:
        """When this block starts for the recurrence on `day` (naive station time)."""
        if block.start_sun_event and station:
            return self._sun_moment(block.start_sun_event, block.start_sun_offset or 0, station, day,
                                    fallback=block.start_time)
        return datetime.combine(day, block.start_time)

    def _block_window(self, block: ScheduleBlock, day, station=None) -> tuple[datetime, datetime]:
        """Start and end of this block for the recurrence on `day` (naive station time).

        An end before the start is the next morning (22:00-02:00), so the part after
        midnight belongs to the day the block started on, and a sun offset that crosses
        midnight keeps the block its real length."""
        start = self._block_start(block, day, station)
        if block.end_sun_event and station:
            end = self._sun_moment(block.end_sun_event, block.end_sun_offset or 0, station, day,
                                   fallback=block.end_time)
        else:
            end = datetime.combine(day, block.end_time)
        if end < start:
            end += timedelta(days=1)
        return start, end

    def _block_matches_time(self, block: ScheduleBlock, at_time: datetime, station=None) -> bool:
        """Check if a schedule block is active at the given time (station local time).

        Looks at the recurrences of yesterday (overnight blocks) and today, and for a
        sun-relative block also tomorrow (sunrise - 60 min can fall before midnight)."""
        now = at_time.replace(tzinfo=None)
        today = now.date()
        sun = bool(station and (block.start_sun_event or block.end_sun_event))
        for back in ((-1, 0, 1) if sun else (0, 1)):
            day = today - timedelta(days=back)
            if not self._recurs_on(block, day):
                continue
            start, end = self._block_window(block, day, station)
            if start <= now < end:
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

        mode = getattr(block, "playback_mode", PlaybackMode.SEQUENTIAL)

        if mode == PlaybackMode.SHUFFLE:
            return await self._pick_shuffle(block, enabled_entries, station_id)
        elif mode == PlaybackMode.WEIGHTED:
            return await self._pick_weighted(block, enabled_entries)
        else:
            return await self._pick_sequential(block, enabled_entries, station_id)

    async def _pick_sequential(
        self,
        block: ScheduleBlock,
        entries: list[PlaylistEntry],
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
        self,
        block: ScheduleBlock,
        entries: list[PlaylistEntry],
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

        # The hourly station ID, time check and weather are queued by the hourly pass
        # (QueueReplenishService._schedule_hourly_announcements) at the station's own
        # hh:00. They were also inserted here on the UTC clock (UTC minute < 2): an
        # extra or misplaced time clip for a station in a half-hour zone (V1-73).

        # Determine current slot by counting total plays for this station modulo slot count
        stmt = select(func.count(PlayLog.id)).where(PlayLog.station_id == station_id)
        result = await self.db.execute(stmt)
        total_plays = result.scalar() or 0

        slot_index = total_plays % len(slots)
        current_slot = slots[slot_index]

        return await self._pick_asset_for_slot(
            current_slot.asset_type, current_slot.category, station_id
        )

    async def _pick_asset_for_slot(
        self, asset_type: str, category: str | None, station_id: UUID | str | None
    ) -> Optional[UUID]:
        """Pick a random asset matching asset_type+category, avoiding recently played and do_not_play."""
        stmt = select(Asset).where(Asset.asset_type == asset_type)
        if category:
            stmt = stmt.where(
                in_category(category), Asset.category != "do_not_play", not_placeholder(),
                not_contract_audio(), not_holiday_only(),
            )
        else:
            # never do-not-play, a dated weather report or an hour's time check
            stmt = stmt.where(fit_for_random_pick())
        if station_id is not None:
            from app.services.station_blocks import blocked_asset_ids

            stmt = stmt.where(Asset.id.notin_(blocked_asset_ids(station_id)))
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
        Uses upsert-style logic to avoid race conditions.
        """
        now = datetime.now(timezone.utc)
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

        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            # Race condition: another process inserted first — fetch and update
            result = await self.db.execute(stmt)
            record = result.scalar_one_or_none()
            if record:
                record.asset_id = asset_id
                record.started_at = now
                record.ends_at = ends_at
                record.block_id = block_id
                await self.db.commit()
            else:
                raise

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
