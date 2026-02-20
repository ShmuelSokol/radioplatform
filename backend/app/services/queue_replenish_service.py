"""Smart queue replenishment service using ScheduleRule logic.

Station-specific rules (station_id = this station) take full priority over
global rules (station_id IS NULL).  If a station has ANY station-specific
rules, global rules are completely ignored for that station — allowing
dedicated content streams like "stories only" without affecting other stations.
"""
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
from app.models.sponsor import Sponsor

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

        Station-specific rules override global rules entirely — a station with
        its own rules (e.g. stories-only) won't receive global music/spots.
        Gap fill uses the primary daypart rule's asset_type, not hardcoded music.
        """
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

        result = await self.db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0))
            .where(QueueEntry.station_id == self.station_id, QueueEntry.status == "pending")
        )
        self.max_pos = result.scalar() or 0

        result = await self.db.execute(
            select(QueueEntry.asset_id).where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
        )
        self.queued_ids = {row[0] for row in result.all()}

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

        rules = await self._load_rules()
        station_specific = any(r.station_id is not None for r in rules)

        if not rules:
            logger.info("No active rules found, falling back to random music fill")
            await self._fill_content(shortfall, asset_type="music", category=None)
            await self.db.flush()
            return

        logger.info("Found %d active rules for replenishment (station_specific=%s)", len(rules), station_specific)

        for rule in rules:
            if rule.rule_type == "rotation":
                await self._apply_rotation_rule(rule, shortfall)
            elif rule.rule_type == "interval":
                await self._apply_interval_rule(rule, shortfall)
            elif rule.rule_type == "daypart":
                await self._apply_daypart_rule(rule, shortfall)
            elif rule.rule_type == "fixed_time":
                await self._apply_fixed_time_rule(rule, shortfall)

        # Only insert global sponsors for stations using global rules
        if not station_specific:
            await self._insert_sponsors(shortfall)

        current_duration = await self._calculate_queue_duration()
        remaining_shortfall = TARGET_QUEUE_SECONDS - current_duration
        if remaining_shortfall > 0:
            fill_type, fill_cat = self._get_fill_spec(rules)
            await self._fill_content(remaining_shortfall, asset_type=fill_type, category=fill_cat)

        await self.db.flush()
        logger.info("Queue replenishment complete")

    async def _load_rules(self) -> list[ScheduleRule]:
        """Load active rules for this station.

        Station-specific rules take priority. If ANY exist for this station,
        global rules (station_id IS NULL) are ignored entirely.
        """
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_day = now.weekday()

        result = await self.db.execute(
            select(ScheduleRule)
            .where(
                ScheduleRule.is_active == True,
                ScheduleRule.station_id == self.station_id,
                ScheduleRule.hour_start <= current_hour,
                ScheduleRule.hour_end >= current_hour,
            )
            .order_by(ScheduleRule.priority.desc())
        )
        station_rules = [
            r for r in result.scalars().all()
            if str(current_day) in (r.days_of_week or "0,1,2,3,4,5,6").split(",")
        ]

        if station_rules:
            logger.info(
                "Station %s: using %d station-specific rule(s), ignoring global rules",
                self.station_id, len(station_rules),
            )
            return station_rules

        result = await self.db.execute(
            select(ScheduleRule)
            .where(
                ScheduleRule.is_active == True,
                ScheduleRule.station_id.is_(None),
                ScheduleRule.hour_start <= current_hour,
                ScheduleRule.hour_end >= current_hour,
            )
            .order_by(ScheduleRule.priority.desc())
        )
        return [
            r for r in result.scalars().all()
            if str(current_day) in (r.days_of_week or "0,1,2,3,4,5,6").split(",")
        ]

    def _get_fill_spec(self, rules: list[ScheduleRule]) -> tuple[str, str | None]:
        """Return (asset_type, category) for gap-filling from the top daypart rule."""
        for rule in rules:
            if rule.rule_type == "daypart":
                return rule.asset_type, rule.category
        return "music", None

    async def _calculate_queue_duration(self) -> float:
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
        if not rule.songs_between or rule.songs_between <= 0:
            return
        avg_song_duration = 180
        estimated_songs = shortfall / avg_song_duration
        insertions_needed = int(estimated_songs / rule.songs_between)
        if insertions_needed <= 0:
            return
        logger.info("Rotation rule '%s': inserting %d x %s", rule.name, insertions_needed, rule.asset_type)
        assets = await self._find_assets_for_rule(rule)
        if not assets:
            return
        for i in range(insertions_needed):
            asset = assets[i % len(assets)]
            insert_pos = self.max_pos + (i + 1) * rule.songs_between
            await self._enqueue_asset(asset, insert_pos)

    async def _apply_interval_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        if not rule.interval_minutes or rule.interval_minutes <= 0:
            return
        interval_seconds = rule.interval_minutes * 60
        insertions_needed = int(shortfall / interval_seconds)
        if insertions_needed <= 0:
            return
        logger.info("Interval rule '%s': inserting %d x %s (every %d min)",
                    rule.name, insertions_needed, rule.asset_type, rule.interval_minutes)
        assets = await self._find_assets_for_rule(rule)
        if not assets:
            return
        for i in range(insertions_needed):
            asset = assets[i % len(assets)]
            time_offset = (i + 1) * interval_seconds
            insert_pos = self.max_pos + int((time_offset / DEFAULT_DURATION))
            await self._enqueue_asset(asset, insert_pos)

    async def _apply_daypart_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        """Passive — defines fill spec via _get_fill_spec."""
        logger.debug("Daypart rule '%s': asset_type=%s category=%s", rule.name, rule.asset_type, rule.category)

    async def _apply_fixed_time_rule(self, rule: ScheduleRule, shortfall: float) -> None:
        logger.debug("Fixed-time rule '%s': skipping (real-time engine)", rule.name)

    async def _find_assets_for_rule(self, rule: ScheduleRule) -> Sequence[Asset]:
        query = select(Asset).where(Asset.asset_type == rule.asset_type)
        if rule.category:
            query = query.where(Asset.category == rule.category)
        if self.exclude_ids:
            query = query.where(Asset.id.notin_(self.exclude_ids))
        query = query.order_by(func.random()).limit(50)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def _enqueue_asset(self, asset: Asset, position: int) -> None:
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

    async def _insert_sponsors(self, shortfall: float) -> None:
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        result = await self.db.execute(select(Sponsor).order_by(Sponsor.priority.desc()))
        sponsors = result.scalars().all()
        for sponsor in sponsors:
            sponsor_asset = None
            # Look up by sponsor_id FK first, fall back to audio_file_path match
            result = await self.db.execute(
                select(Asset).where(
                    (Asset.sponsor_id == sponsor.id) | (Asset.file_path == sponsor.audio_file_path)
                ).limit(1)
            )
            sponsor_asset = result.scalar_one_or_none()
            if not sponsor_asset:
                continue
            rules = sponsor.target_rules or {}
            hour_start = rules.get("hour_start", 0)
            hour_end = rules.get("hour_end", 24)
            max_per_hour = rules.get("max_per_hour", 4)
            songs_between = rules.get("songs_between", 6)
            if not (hour_start <= current_hour < hour_end):
                continue
            if sponsor.insertion_policy == "every_n_songs":
                insertions = min(int((shortfall / DEFAULT_DURATION) / max(songs_between, 1)), max_per_hour)
                for i in range(insertions):
                    self.max_pos += songs_between
                    await self._enqueue_asset(sponsor_asset, self.max_pos)
            elif sponsor.insertion_policy == "fixed_interval":
                interval_sec = rules.get("interval_minutes", 15) * 60
                insertions = min(int(shortfall / interval_sec), max_per_hour)
                for i in range(insertions):
                    pos = self.max_pos + int(((i + 1) * interval_sec) / DEFAULT_DURATION)
                    await self._enqueue_asset(sponsor_asset, pos)
            else:
                insertions = min(int(shortfall / (DEFAULT_DURATION * max(songs_between, 1))), max_per_hour)
                for i in range(insertions):
                    self.max_pos += songs_between
                    await self._enqueue_asset(sponsor_asset, self.max_pos)

    async def _fill_content(
        self,
        shortfall: float,
        asset_type: str = "music",
        category: str | None = None,
    ) -> None:
        """Fill remaining queue with random content of the given type/category."""
        logger.info("Filling %.1fs: type=%s category=%s", shortfall, asset_type, category)
        query = select(Asset).where(Asset.asset_type == asset_type)
        if category:
            query = query.where(Asset.category == category)
        if self.exclude_ids:
            query = query.where(Asset.id.notin_(self.exclude_ids))
        query = query.order_by(func.random())
        result = await self.db.execute(query)
        candidates = result.scalars().all()

        if not candidates:
            logger.warning("No %s/%s candidates, retrying without recent exclusion", asset_type, category)
            query = select(Asset).where(Asset.asset_type == asset_type)
            if category:
                query = query.where(Asset.category == category)
            if self.queued_ids:
                query = query.where(Asset.id.notin_(self.queued_ids))
            query = query.order_by(func.random())
            result = await self.db.execute(query)
            candidates = result.scalars().all()

        if not candidates:
            logger.warning("No %s assets available", asset_type)
            return

        filled = 0.0
        for asset in candidates:
            if filled >= shortfall:
                break
            dur = asset.duration or DEFAULT_DURATION
            self.max_pos += 1
            await self._enqueue_asset(asset, self.max_pos)
            filled += dur

        logger.info("Filled %.1fs of %s content", filled, asset_type)

    # Backwards-compat alias
    async def _fill_random_music(self, shortfall: float) -> None:
        await self._fill_content(shortfall, asset_type="music", category=None)
