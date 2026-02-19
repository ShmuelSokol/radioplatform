"""Smart queue replenishment service using ScheduleRule logic."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.schedule_rule import ScheduleRule

logger = logging.getLogger(__name__)

TARGET_QUEUE_SECONDS = 86400  # 24 hours
DEFAULT_DURATION = 180  # 3 minutes fallback


class QueueReplenishService:
    """Handles smart queue replenishment using schedule rules."""

    def __init__(self, db: AsyncSession, station_id: uuid.UUID):
        self.db = db
        self.station_id = station_id

    async def replenish(self) -> None:
        """
        Auto-fill queue to ~24 hours of content using active schedule rules.
        
        Strategy:
        1. Calculate current queue duration
        2. If below 24h, fetch active rules sorted by priority (desc)
        3. Apply each rule type:
           - rotation: Insert spot/jingle every N songs
           - interval: Insert spot every N minutes
           - daypart: Fill time blocks with category-specific content
           - fixed_time: Schedule specific assets at exact times
        4. Fill remaining gaps with music (respecting category constraints)
        """
        # Sum durations of pending + playing entries
        result = await self.db.execute(
            select(func.coalesce(func.sum(func.coalesce(Asset.duration, DEFAULT_DURATION)), 0))
            .select_from(QueueEntry)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
        )
        total_seconds = float(result.scalar() or 0)

        if total_seconds >= TARGET_QUEUE_SECONDS:
            logger.debug("Queue already full (%.1fs), skipping replenish", total_seconds)
            return

        shortfall = TARGET_QUEUE_SECONDS - total_seconds
        logger.info("Queue shortfall: %.1fs, replenishing...", shortfall)

        # Get current max position
        result = await self.db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0))
            .where(QueueEntry.station_id == self.station_id, QueueEntry.status == "pending")
        )
        self.max_pos = result.scalar() or 0

        # Get IDs already in queue
        result = await self.db.execute(
            select(QueueEntry.asset_id).where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
        )
        self.queued_ids = {row[0] for row in result.all()}

        # Get asset IDs played in last 2 hours (avoid repeats)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await self.db.execute(
            select(PlayLog.asset_id).where(
                PlayLog.station_id == self.station_id,
                PlayLog.start_utc >= cutoff,
                PlayLog.asset_id.isnot(None),
            )
        )
        recent_ids = {row[0] for row in result.all()}
        self.exclude_ids = self.queued_ids | recent_ids

        # Fetch active rules sorted by priority
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_day = now.weekday()  # 0=Mon, 6=Sun

        result = await self.db.execute(
            select(ScheduleRule)
            .where(
                ScheduleRule.is_active == True,
                ScheduleRule.hour_start <= current_hour,
                ScheduleRule.hour_end >= current_hour,
            )
            .order_by(ScheduleRule.priority.desc())
        )
        rules = result.scalars().all()

        # Filter by day of week
        rules = [
            r for r in rules
            if str(current_day) in (r.days_of_week or "0,1,2,3,4,5,6").split(",")
        ]

        if not rules:
            logger.info("No active rules found, falling back to random music fill")
            await self._fill_random_music(shortfall)
            await self.db.flush()
            return

        logger.info("Found %d active rules for replenishment", len(rules))

        # Apply each rule type
        for rule in rules:
            if rule.rule_type == "rotation":
                await self._apply_rotation_rule(rule, shortfall)
            elif rule.rule_type == "interval":
                await self._apply_interval_rule(rule, shortfall)
            elif rule.rule_type == "daypart":
                await self._apply_daypart_rule(rule, shortfall)
            elif rule.rule_type == "fixed_time":
                await self._apply_fixed_time_rule(rule, shortfall)

        # Fill remaining gaps with music
        current_duration = await self._calculate_queue_duration()
        remaining_shortfall = TARGET_QUEUE_SECONDS - current_duration
        if remaining_shortfall > 0:
            await self._fill_random_music(remaining_shortfall)

        await self.db.flush()
        logger.info("Queue replenishment complete")

    async def _calculate_queue_duration(self) -> float:
        """Calculate total duration of pending + playing queue entries."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(func.coalesce(Asset.duration, DEFAULT_DURATION)), 0))
            .select_from(QueueEntry)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
        )
        return float(result.scalar() or 0)

    async def _apply_rotation_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        """
        Apply rotation rule: Insert spot/jingle every N songs.
        Example: "Play hourly jingle every 12 songs"
        """
        if not rule.songs_between or rule.songs_between <= 0:
            return

        # Count music tracks already in queue
        result = await self.db.execute(
            select(func.count(QueueEntry.id))
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status.in_(["pending", "playing"]),
                Asset.asset_type == "music",
            )
        )
        music_count = result.scalar() or 0

        # How many times should we insert in the upcoming shortfall?
        # Estimate: shortfall / avg_song_duration * (1 / songs_between)
        avg_song_duration = 180  # 3 min
        estimated_songs = shortfall / avg_song_duration
        insertions_needed = int(estimated_songs / rule.songs_between)

        if insertions_needed <= 0:
            return

        logger.info(
            "Rotation rule '%s': inserting %d x %s (every %d songs)",
            rule.name, insertions_needed, rule.asset_type, rule.songs_between
        )

        # Find candidate assets
        assets = await self._find_assets_for_rule(rule)
        if not assets:
            logger.warning("No assets found for rotation rule '%s'", rule.name)
            return

        # Insert at intervals of N songs
        for i in range(insertions_needed):
            asset = assets[i % len(assets)]  # Rotate through available assets
            # Calculate position: current queue position + (i+1) * songs_between
            insert_pos = self.max_pos + (i + 1) * rule.songs_between
            await self._enqueue_asset(asset, insert_pos)

    async def _apply_interval_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        """
        Apply interval rule: Insert spot every N minutes.
        Example: "Weather spot every 15 minutes"
        """
        if not rule.interval_minutes or rule.interval_minutes <= 0:
            return

        # How many times in the shortfall?
        interval_seconds = rule.interval_minutes * 60
        insertions_needed = int(shortfall / interval_seconds)

        if insertions_needed <= 0:
            return

        logger.info(
            "Interval rule '%s': inserting %d x %s (every %d min)",
            rule.name, insertions_needed, rule.asset_type, rule.interval_minutes
        )

        assets = await self._find_assets_for_rule(rule)
        if not assets:
            logger.warning("No assets found for interval rule '%s'", rule.name)
            return

        # Insert at time-based intervals
        current_duration = await self._calculate_queue_duration()
        for i in range(insertions_needed):
            asset = assets[i % len(assets)]
            # Calculate position based on time offset
            time_offset = (i + 1) * interval_seconds
            insert_pos = self.max_pos + int((time_offset / DEFAULT_DURATION))
            await self._enqueue_asset(asset, insert_pos)

    async def _apply_daypart_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        """
        Apply daypart rule: Fill specific hours with category-specific music.
        Example: "Play shiurim from 8am-10am"
        """
        # For simplicity, daypart rules just constrain what music gets added
        # This is more of a filter than an insertion — we'll handle in _fill_random_music
        logger.debug(
            "Daypart rule '%s': filtering music by category '%s' during %d-%d",
            rule.name, rule.category, rule.hour_start, rule.hour_end
        )
        # Daypart logic is passive — it affects _fill_random_music category selection

    async def _apply_fixed_time_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        """
        Apply fixed_time rule: Schedule specific asset at exact time.
        Example: "News at top of hour"
        
        Note: This is best handled by hourly jingle insertion (_maybe_insert_hourly_jingle)
        For now, we'll skip implementation since it requires real-time scheduling.
        """
        logger.debug("Fixed-time rule '%s': skipping (handled by real-time engine)", rule.name)

    async def _find_assets_for_rule(self, rule: ScheduleRule) -> Sequence[Asset]:
        """Find assets matching a rule's asset_type and category."""
        query = select(Asset).where(Asset.asset_type == rule.asset_type)

        if rule.category:
            query = query.where(Asset.category == rule.category)

        # Exclude already queued/recent
        if self.exclude_ids:
            query = query.where(Asset.id.notin_(self.exclude_ids))

        query = query.order_by(func.random()).limit(50)  # Get pool of candidates
        result = await self.db.execute(query)
        return result.scalars().all()

    async def _enqueue_asset(self, asset: Asset, position: int) -> None:
        """Add an asset to the queue at a specific position."""
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=self.station_id,
            asset_id=asset.id,
            position=position,
            status="pending",
        )
        self.db.add(entry)
        self.queued_ids.add(asset.id)
        self.max_pos = max(self.max_pos, position)
        logger.debug("Enqueued %s '%s' at position %d", asset.asset_type, asset.title, position)

    async def _fill_random_music(self, shortfall: float) -> None:
        """Fill remaining queue with random music, respecting daypart category constraints."""
        # Check for active daypart rules to filter category
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_day = now.weekday()

        result = await self.db.execute(
            select(ScheduleRule)
            .where(
                ScheduleRule.is_active == True,
                ScheduleRule.rule_type == "daypart",
                ScheduleRule.hour_start <= current_hour,
                ScheduleRule.hour_end >= current_hour,
            )
            .order_by(ScheduleRule.priority.desc())
        )
        daypart_rules = result.scalars().all()
        daypart_rules = [
            r for r in daypart_rules
            if str(current_day) in (r.days_of_week or "0,1,2,3,4,5,6").split(",")
        ]

        category_filter = None
        if daypart_rules:
            # Use highest priority daypart rule's category
            category_filter = daypart_rules[0].category
            logger.info("Filling music with category filter: %s", category_filter)

        # Select random music
        query = select(Asset).where(Asset.asset_type == "music")

        if category_filter:
            query = query.where(Asset.category == category_filter)

        if self.exclude_ids:
            query = query.where(Asset.id.notin_(self.exclude_ids))

        query = query.order_by(func.random())
        result = await self.db.execute(query)
        candidates = result.scalars().all()

        if not candidates:
            # If all excluded, try again without recent exclusion (only skip queued)
            logger.warning("No music candidates found, retrying with relaxed constraints")
            query = select(Asset).where(Asset.asset_type == "music")
            if category_filter:
                query = query.where(Asset.category == category_filter)
            if self.queued_ids:
                query = query.where(Asset.id.notin_(self.queued_ids))
            query = query.order_by(func.random())
            result = await self.db.execute(query)
            candidates = result.scalars().all()

        if not candidates:
            logger.warning("No music assets available for queue fill")
            return

        filled = 0.0
        for asset in candidates:
            if filled >= shortfall:
                break
            dur = asset.duration or DEFAULT_DURATION
            self.max_pos += 1
            await self._enqueue_asset(asset, self.max_pos)
            filled += dur

        logger.info("Filled %.1fs of music (%.1f%% of shortfall)", filled, (filled / shortfall) * 100)
