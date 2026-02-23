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

from datetime import date

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.schedule_rule import ScheduleRule
from app.models.song_request import SongRequest
from app.models.sponsor import Sponsor
from app.models.station import Station

logger = logging.getLogger(__name__)

TARGET_QUEUE_SECONDS = 604800  # 7 days
DEFAULT_DURATION = 180  # 3 minutes fallback
MAX_ADS_PER_RUN = 504  # 7 days × 24h × 3 quarter-hour slots


class QueueReplenishService:
    """Handles smart queue replenishment using schedule rules."""

    def __init__(self, db: AsyncSession, station_id: uuid.UUID):
        self.db = db
        self.station_id = station_id
        self.automation_config: dict = {}

    async def _load_automation_config(self) -> None:
        """Load the station's automation_config JSONB."""
        result = await self.db.execute(
            select(Station.automation_config).where(Station.id == self.station_id)
        )
        self.automation_config = result.scalar() or {}

    async def replenish(self) -> None:
        """
        Auto-fill queue to ~24 hours of content using active schedule rules.

        Station-specific rules override global rules entirely — a station with
        its own rules (e.g. stories-only) won't receive global music/spots.
        Gap fill uses the primary daypart rule's asset_type, not hardcoded music.

        Special modes (via station.automation_config):
        - requests_only: fill queue from approved song requests + popular requests
        - oldies_only: only queue songs with release_date 10+ years ago
        """
        await self._load_automation_config()

        # Requests-only mode: entirely different fill strategy
        if self.automation_config.get("requests_only"):
            await self._replenish_requests_only()
            return

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

        # Always schedule hourly announcements + ad slots, even if queue is "full"
        result2 = await self.db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0))
            .where(QueueEntry.station_id == self.station_id, QueueEntry.status == "pending")
        )
        self.max_pos = result2.scalar() or 0

        if self.automation_config.get("hourly_time_announcement") or self.automation_config.get("weather_enabled"):
            await self._schedule_hourly_announcements()

        await self._schedule_ad_slots()
        await self.db.flush()

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

        # Step 1: Apply daypart / fixed_time rules (passive — define fill spec)
        for rule in rules:
            if rule.rule_type == "daypart":
                await self._apply_daypart_rule(rule, shortfall)
            elif rule.rule_type == "fixed_time":
                await self._apply_fixed_time_rule(rule, shortfall)

        # Step 2: Fill main content FIRST so interval/rotation entries interleave
        fill_type, fill_cat = self._get_fill_spec(rules)
        fill_start_pos = self.max_pos
        await self._fill_content(shortfall, asset_type=fill_type, category=fill_cat)
        fill_end_pos = self.max_pos

        # Only insert global sponsors for stations using global rules
        if not station_specific:
            await self._insert_sponsors(shortfall)

        # Step 3: Apply rotation rules (spread within fill range)
        for rule in rules:
            if rule.rule_type == "rotation":
                await self._apply_rotation_rule(rule, shortfall)

        # Step 4: Pre-schedule hourly time + weather with exact preempt_at timestamps
        if self.automation_config.get("hourly_time_announcement") or self.automation_config.get("weather_enabled"):
            await self._schedule_hourly_announcements()

        await self._schedule_ad_slots()
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

    async def _apply_interval_rule(
        self, rule: ScheduleRule, shortfall: float,
        fill_start: int | None = None, fill_end: int | None = None,
    ) -> None:
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

        # If fill range is provided, spread entries evenly within it
        if fill_start is not None and fill_end is not None and fill_end > fill_start:
            span = fill_end - fill_start
            step = max(1, span // (insertions_needed + 1))
            for i in range(insertions_needed):
                asset = assets[i % len(assets)]
                insert_pos = fill_start + (i + 1) * step
                await self._enqueue_asset(asset, insert_pos)
        else:
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

    def _apply_oldies_filter(self, query):
        """If station has oldies_only config, restrict to old release dates."""
        if self.automation_config.get("oldies_only"):
            min_years = self.automation_config.get("oldies_min_years", 10)
            cutoff = date.today() - timedelta(days=min_years * 365)
            query = query.where(Asset.release_date.isnot(None), Asset.release_date <= cutoff)
        return query

    async def _find_assets_for_rule(self, rule: ScheduleRule) -> Sequence[Asset]:
        query = select(Asset).where(Asset.asset_type == rule.asset_type)
        if rule.category:
            query = query.where(Asset.category == rule.category)
        if self.exclude_ids:
            query = query.where(Asset.id.notin_(self.exclude_ids))
        query = self._apply_oldies_filter(query)
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
        query = self._apply_oldies_filter(query)
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
            query = self._apply_oldies_filter(query)
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

    async def _replenish_requests_only(self) -> None:
        """Fill queue exclusively from song requests (approved/queued) + popular requests."""
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
            return

        shortfall = TARGET_QUEUE_SECONDS - total_seconds

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
        self.exclude_ids = set(self.queued_ids)

        filled = 0.0

        # 1. Approved/queued song requests with matched assets
        result = await self.db.execute(
            select(SongRequest)
            .where(
                SongRequest.station_id == self.station_id,
                SongRequest.status.in_(["APPROVED", "QUEUED"]),
                SongRequest.asset_id.isnot(None),
            )
            .order_by(SongRequest.created_at)
        )
        requests = result.scalars().all()
        for req in requests:
            if filled >= shortfall:
                break
            if req.asset_id in self.queued_ids:
                continue
            asset_result = await self.db.execute(
                select(Asset).where(Asset.id == req.asset_id)
            )
            asset = asset_result.scalar_one_or_none()
            if not asset:
                continue
            self.max_pos += 1
            await self._enqueue_asset(asset, self.max_pos)
            filled += asset.duration or DEFAULT_DURATION
            # Mark request as queued
            req.status = "QUEUED"

        # 2. If still short, auto-fill from popular requests (3+ in last 30 days)
        if filled < shortfall:
            threshold = self.automation_config.get("popular_request_threshold", 3)
            cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
            popular_result = await self.db.execute(
                select(SongRequest.asset_id, func.count(SongRequest.id).label("cnt"))
                .where(
                    SongRequest.asset_id.isnot(None),
                    SongRequest.created_at >= cutoff_30d,
                )
                .group_by(SongRequest.asset_id)
                .having(func.count(SongRequest.id) >= threshold)
                .order_by(func.random())
            )
            popular_rows = popular_result.all()
            for row in popular_rows:
                if filled >= shortfall:
                    break
                asset_id = row[0]
                if asset_id in self.queued_ids:
                    continue
                asset_result = await self.db.execute(
                    select(Asset).where(Asset.id == asset_id)
                )
                asset = asset_result.scalar_one_or_none()
                if not asset:
                    continue
                self.max_pos += 1
                await self._enqueue_asset(asset, self.max_pos)
                filled += asset.duration or DEFAULT_DURATION

        await self.db.flush()
        logger.info("Requests-only replenish: filled %.1fs", filled)

    async def _schedule_hourly_announcements(self) -> None:
        """Pre-schedule time announcement + weather at each hour boundary with preempt_at.

        These entries use preempt_at so they interrupt the current track exactly
        on the hour — no reliance on scheduler timing.
        """
        from app.config import settings
        if not settings.elevenlabs_enabled or not settings.supabase_storage_enabled:
            logger.info("Skipping hourly announcements: TTS or storage not configured")
            return

        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        from app.services.weather_spot_service import get_or_create_weather_spot_assets

        now = datetime.now(timezone.utc)
        eastern_tz = ZoneInfo("America/New_York")

        # Check which hours already have scheduled entries (avoid duplicates)
        result = await self.db.execute(
            select(QueueEntry.preempt_at)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
            )
        )
        existing_preempts = {row[0].replace(minute=0, second=0, microsecond=0) for row in result.all() if row[0]}

        # Schedule for the next 48 hours (weather data only reliable ~48h out;
        # future runs will extend as hours come within range)
        hours_scheduled = 0
        for h in range(48):
            hour_boundary = (now + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
            if hour_boundary <= now:
                continue  # Skip past hours
            if hour_boundary in existing_preempts:
                continue  # Already scheduled

            eastern_time = hour_boundary.astimezone(eastern_tz)
            slot_key = eastern_time.strftime("%Y-%m-%dT%H")

            try:
                time_asset, weather_asset = await get_or_create_weather_spot_assets(self.db, slot_key)
            except Exception:
                logger.warning("Failed to generate weather for slot %s", slot_key, exc_info=True)
                continue

            assets_to_insert = [a for a in [time_asset, weather_asset] if a is not None]
            if not assets_to_insert:
                continue

            for i, asset in enumerate(assets_to_insert):
                self.max_pos += 1
                entry = QueueEntry(
                    id=uuid.uuid4(),
                    station_id=self.station_id,
                    asset_id=asset.id,
                    position=self.max_pos,
                    status="pending",
                    preempt_at=hour_boundary,  # ALL entries get preempt_at so weather follows time
                )
                self.db.add(entry)

            hours_scheduled += 1
            logger.info("Scheduled time+weather for %s (slot %s)", hour_boundary.isoformat(), slot_key)

        logger.info("Scheduled hourly announcements for %d hours", hours_scheduled)

    async def _schedule_ad_slots(self) -> None:
        """Schedule sponsor ad at :15, :30, :45 of every hour (soft preempt).

        Ads use preempt_at so they play when their clock time arrives — the
        playback engine waits for the current song to finish, then plays the
        ad before the next regular song.
        """
        # Find the ad asset
        result = await self.db.execute(
            select(Asset).where(Asset.title == "KOL BRAMAH TEST SPONSOR").limit(1)
        )
        ad_asset = result.scalar_one_or_none()
        if not ad_asset:
            logger.warning("Ad asset 'KOL BRAMAH TEST SPONSOR' not found")
            return

        now = datetime.now(timezone.utc)

        # Get existing ad_slot preempt times to avoid duplicates
        existing = await self.db.execute(
            select(QueueEntry.preempt_at).where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.source == "ad_slot",
                QueueEntry.preempt_at.isnot(None),
            )
        )
        existing_times = {
            r[0].replace(second=0, microsecond=0)
            for r in existing.all() if r[0]
        }

        # Schedule for 7 days (7×24×3 = 504 quarter-hour slots)
        inserted = 0
        for hours_ahead in range(168):  # 7 days
            for minute in (15, 30, 45):
                slot_time = (now + timedelta(hours=hours_ahead)).replace(
                    minute=minute, second=0, microsecond=0
                )
                if slot_time <= now or slot_time in existing_times:
                    continue
                if inserted >= MAX_ADS_PER_RUN:
                    break

                self.max_pos += 1
                entry = QueueEntry(
                    id=uuid.uuid4(),
                    station_id=self.station_id,
                    asset_id=ad_asset.id,
                    position=self.max_pos,
                    status="pending",
                    source="ad_slot",
                    preempt_at=slot_time,
                )
                self.db.add(entry)
                inserted += 1
            if inserted >= MAX_ADS_PER_RUN:
                break

        if inserted:
            logger.info("Scheduled %d clock-based ad slots for station %s", inserted, self.station_id)

    # Backwards-compat alias
    async def _fill_random_music(self, shortfall: float) -> None:
        await self._fill_content(shortfall, asset_type="music", category=None)
