"""Smart queue replenishment service using ScheduleRule logic.

Station-specific rules (station_id = this station) take full priority over
global rules (station_id IS NULL).  If a station has ANY station-specific
rules, global rules are completely ignored for that station — allowing
dedicated content streams like "stories only" without affecting other stations.
"""
import dataclasses
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import date

from app.models.asset import ONE_MOMENT_CATEGORIES, Asset, fit_for_random_pick, in_category, not_placeholder
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



def local_hour_marks(now: datetime, zone, hours: int) -> list[datetime]:
    """The UTC moments in the next `hours` hours at which the station's own clock reads
    hh:00. Hours were taken on the UTC clock, so a station in a half-hour zone (India,
    UTC+5:30) heard its time check at local hh:30. Every zone offset in use is a
    multiple of 15 minutes, so stepping through UTC quarter hours finds each local
    hour; on the autumn DST day 1 AM comes twice (two marks), in spring 2 AM none."""
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    marks = []
    for k in range(hours * 4 + 1):
        at = base + timedelta(minutes=15 * k)
        if at > now and at.astimezone(zone).minute == 0:
            marks.append(at)
    return marks

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

        from app.services.playlist_generator_service import settings_for
        from app.services.schedule_refresh_service import (
            automatic_songs,
            drop_dated_clips,
            realign_before_block_change,
            remove_automatic_songs,
            uses_block_playlists,
        )

        # A weather report or time check queued as an ordinary song is out of date
        await drop_dated_clips(self.db, self.station_id)

        # A station whose schedule blocks have playlists fills from them, a few hours
        # ahead, whether or not "Generate from sequences" was switched on. A week of
        # ordinary fill left from before is replaced, or the blocks would wait for it.
        generation = settings_for(self.automation_config)
        by_schedule = not generation.enabled and await uses_block_playlists(self.db, self.station_id)
        if by_schedule:
            generation = dataclasses.replace(generation, enabled=True)
            queued_auto = await self.db.execute(
                select(func.coalesce(func.sum(func.coalesce(Asset.duration, DEFAULT_DURATION)), 0))
                .select_from(QueueEntry)
                .join(Asset, QueueEntry.asset_id == Asset.id)
                .where(*automatic_songs(self.station_id))
            )
            if float(queued_auto.scalar() or 0) > 2 * generation.hours * 3600:
                await remove_automatic_songs(self.db, self.station_id)
                logger.info("Station %s now fills from its schedule blocks: old fill replaced", self.station_id)
        if by_schedule or (generation.enabled and await uses_block_playlists(self.db, self.station_id)):
            # Shortly before a block's playlist takes over, draw the songs again from
            # the real time on air so the block starts on time, not early
            station = (await self.db.execute(select(Station).where(Station.id == self.station_id))).scalar_one()
            await realign_before_block_change(self.db, station, datetime.now(timezone.utc))
        await self.db.flush()

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
            .where(QueueEntry.station_id == self.station_id, QueueEntry.status.in_(["pending", "playing"]))
        )
        self.max_pos = result2.scalar() or 0

        # Program blocks ("Weather Sponsors": station ID, weather, PSA, sponsor...)
        # replace the built-in hourly time and weather once a station has any.
        from app.services.program_block_service import blocks_keep_hourly, reconcile_block_entries

        wants_hourly = bool(
            self.automation_config.get("hourly_time_announcement")
            or self.automation_config.get("weather_enabled")
        )
        # automation_config.blocks_keep_hourly: this station's blocks play beside the
        # built-in hourly time and weather instead of replacing them. The hour is queued
        # first, so a block due on the hour plays right after the time and weather.
        keep_hourly = blocks_keep_hourly(self.automation_config)
        if keep_hourly and wants_hourly:
            await self._schedule_hourly_announcements()
            await self.db.flush()

        blocks_result = None
        try:
            blocks_result = await reconcile_block_entries(self.db, self.station_id)
        except Exception:
            logger.exception("Program blocks could not be queued for station %s", self.station_id)
        if blocks_result is not None:
            self.max_pos = max(self.max_pos, blocks_result["max_pos"])
        if blocks_result is None and wants_hourly and not keep_hourly:
            await self._schedule_hourly_announcements()

        await self._schedule_ad_slots()
        await self.db.commit()

        # Playlist generation: fill a few hours ahead from the schedule blocks'
        # playlist sequences instead of the rule-based 7-day fill.
        if generation.enabled:
            if await self._fill_from_playlists(generation, total_seconds):
                return
            # Nothing could be generated (no block with a sequence): keep the
            # station on the air with the ordinary fill, but only as far ahead
            # as generation looks, so sequences take over once they exist.
            shortfall = generation.hours * 3600 - total_seconds
            if shortfall > 0:
                self.queued_ids = set()
                self.exclude_ids = set()
                await self._fill_content(shortfall, asset_type="music", category=None)
            return

        if total_seconds >= TARGET_QUEUE_SECONDS:
            logger.info("Queue already full (%.1fs >= %.1fs target), skipping content fill", total_seconds, float(TARGET_QUEUE_SECONDS))
            return

        shortfall = TARGET_QUEUE_SECONDS - total_seconds
        logger.info("Queue shortfall: %.1fs, replenishing...", shortfall)

        result = await self.db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0))
            .where(QueueEntry.station_id == self.station_id, QueueEntry.status.in_(["pending", "playing"]))
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
            return

        logger.info("Found %d active rules for replenishment (station_specific=%s)", len(rules), station_specific)

        # Step 1: Apply daypart / fixed_time rules (passive — define fill spec)
        for rule in rules:
            if rule.rule_type == "daypart":
                await self._apply_daypart_rule(rule, shortfall)
            elif rule.rule_type == "fixed_time":
                await self._apply_fixed_time_rule(rule, shortfall)

        # Step 2: Fill main content FIRST so interval/rotation entries interleave
        # Use time-aware fill so morning/afternoon/evening rules are respected
        # across the full 7-day queue window, not just the current hour's rule.
        fill_start_pos = self.max_pos + 1
        await self._fill_content_time_aware(shortfall, rules)

        # Step 3: Rotation ("every N songs") and interval ("every N minutes") rules
        # are woven into the fill just made, not appended after it
        await self._spread_rule_items(rules, fill_start_pos, total_seconds)

        # Only insert global sponsors for stations using global rules
        if not station_specific:
            await self._insert_sponsors(shortfall)

        await self.db.commit()
        logger.info("Queue replenishment complete")

    async def _realign_weather_region(self, region: str) -> int:
        """Point queued weather spots at the station's own region.

        Weather queued before a station had its own region (or before its spot
        was uploaded) is another city's report. Each such entry is moved to the
        station's own spot for that day when one exists, and removed otherwise
        (that slot then airs without weather rather than with the wrong city's).
        """
        from app.services.weather_spot_service import find_weather_spot_asset, normalize_region

        rows = await self.db.execute(
            select(QueueEntry, Asset)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                Asset.category == "weather_spot",
            )
        )
        changed = 0
        own_spot: dict[str, Asset | None] = {}
        for entry, asset in rows.all():
            meta = asset.metadata_extra if isinstance(asset.metadata_extra, dict) else {}
            if normalize_region(meta.get("region")) == region:
                continue
            day = meta.get("slot_key")
            if day not in own_spot:
                own_spot[day] = await find_weather_spot_asset(self.db, day, region) if day else None
            if own_spot[day] is not None:
                entry.asset_id = own_spot[day].id
            else:
                await self.db.delete(entry)
            changed += 1
        if changed:
            await self.db.flush()
            logger.info("Station %s: %d queued weather spots moved to its own region (%s)", self.station_id, changed, region)
        return changed

    async def _realign_time_clips(self, region: str) -> int:
        """Point queued uploaded time checks at the best clip for the station's region.

        A station plays its own region's clip for an hour, else the default set's.
        Time checks queued before the region's clip was uploaded (or before the
        station had a region) are moved to the clip it should play now.
        """
        from app.services.weather_spot_service import find_time_clip_asset

        rows = await self.db.execute(
            select(QueueEntry, Asset)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                Asset.category == "time_announcement",
            )
        )
        changed = 0
        best: dict[str, Asset | None] = {}
        for entry, asset in rows.all():
            meta = asset.metadata_extra if isinstance(asset.metadata_extra, dict) else {}
            hour = meta.get("time_of_day")
            if not hour:
                continue  # a generated per-slot clip, not an uploaded set
            if hour not in best:
                best[hour] = await find_time_clip_asset(self.db, int(hour), region)
            clip = best[hour]
            if clip is not None and clip.id != asset.id:
                entry.asset_id = clip.id
                changed += 1
        if changed:
            await self.db.flush()
            logger.info("Station %s: %d queued time checks moved to its region's clips (%s)", self.station_id, changed, region)
        return changed

    async def _drop_wrong_hour_announcements(self, zone) -> int:
        """Remove queued hourly time checks that say another clock's hour.

        Entries queued before stations announced their own time said New York's
        hour everywhere. The time check and the weather queued with it (same
        time) are removed, and the hourly pass queues the right ones again.
        """
        from app.services.weather_spot_service import clip_hour

        rows = await self.db.execute(
            select(QueueEntry.preempt_at, Asset)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                or_(QueueEntry.source.is_(None), QueueEntry.source == "auto"),
                Asset.category == "time_announcement",
            )
        )
        wrong = set()
        for at, asset in rows.all():
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            hour = clip_hour(asset)
            if hour is not None and hour != at.astimezone(zone).hour:
                wrong.add(at)
        if not wrong:
            return 0
        result = await self.db.execute(
            delete(QueueEntry)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.in_(wrong),
                or_(QueueEntry.source.is_(None), QueueEntry.source == "auto"),
            )
            .execution_options(synchronize_session=False)
        )
        await self.db.flush()
        logger.info("Station %s: replaced %d time checks queued with another clock's hour", self.station_id, len(wrong))
        return result.rowcount or 0

    async def _air_timeline(self, now: datetime):
        """When newly drawn songs would start, and the timed items still ahead then.

        Walks the queue the way it plays: the item on air until its crossfade
        point, each waiting item for its own on-air length, and items queued for
        set times where they fall (a hard one cuts the item on air).
        """
        from app.models.queue_entry import is_soft_timed
        from app.services.playlist_generator_service import Clock, TimedItem, on_air_seconds

        rows = (
            await self.db.execute(
                select(QueueEntry, Asset.duration, Asset.metadata_extra)
                .join(Asset, QueueEntry.asset_id == Asset.id)
                .where(
                    QueueEntry.station_id == self.station_id,
                    QueueEntry.status.in_(["pending", "playing"]),
                )
                .order_by(QueueEntry.position)
            )
        ).all()

        def aware(dt: datetime) -> datetime:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        start = now
        timed: list = []
        waiting: list[float] = []
        for entry, duration, meta in rows:
            seconds = on_air_seconds(duration, meta if isinstance(meta, dict) else None)
            if entry.status == "playing":
                if entry.started_at is not None:
                    start = max(start, aware(entry.started_at) + timedelta(seconds=seconds))
            elif entry.preempt_at is not None:
                timed.append(TimedItem(max(aware(entry.preempt_at), now), seconds, not is_soft_timed(entry)))
            else:
                waiting.append(seconds)
        clock = Clock(start, timed)
        for seconds in waiting:
            clock.play(seconds)
        return clock.start(), clock.timed

    async def _fill_from_playlists(self, generation, queued_seconds: float) -> bool:
        """Top the queue up to `generation.hours` from playlist sequences.

        Returns False when nothing could be generated at all.
        """
        from app.services.playlist_generator_service import CONFIG_KEY, generate

        target = generation.hours * 3600
        now = datetime.now(timezone.utc)
        fill_start, timed = await self._air_timeline(now)
        queued_seconds = (fill_start - now).total_seconds()
        if queued_seconds >= target:
            return True
        station = (
            await self.db.execute(select(Station).where(Station.id == self.station_id))
        ).scalar_one()
        pending = (
            await self.db.execute(
                select(QueueEntry.asset_id, Asset.artist)
                .join(Asset, QueueEntry.asset_id == Asset.id)
                .where(
                    QueueEntry.station_id == self.station_id,
                    QueueEntry.status.in_(["pending", "playing"]),
                )
                .order_by(QueueEntry.position)
            )
        ).all()
        self.queued_ids = {row[0] for row in pending}
        config = dict(station.automation_config or {})
        saved = dict(config.get(CONFIG_KEY) or {})
        plan = await generate(
            self.db,
            station,
            fill_start,
            (target - queued_seconds) / 3600,
            artist_distance=generation.artist_distance,
            min_vote=generation.min_vote,
            cursor=saved.get("cursor") or {},
            recent_artists=[row[1] for row in pending][-20:],
            busy_ids=set(self.queued_ids),
            timed=timed,
        )
        if not plan.picks:
            logger.info("Playlist generation produced nothing: %s", "; ".join(plan.notes))
            return False
        result = await self.db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0)).where(
                QueueEntry.station_id == self.station_id, QueueEntry.status.in_(["pending", "playing"])
            )
        )
        position = result.scalar() or 0
        for pick in plan.picks:
            position += 1
            self.db.add(
                QueueEntry(
                    id=uuid.uuid4(),
                    station_id=self.station_id,
                    asset_id=pick.asset_id,
                    position=position,
                    status="pending",
                    block_id=pick.block_id or None,
                )
            )
        saved["cursor"] = plan.cursor
        config[CONFIG_KEY] = saved
        station.automation_config = config
        await self.db.commit()
        logger.info("Playlist generation queued %d items", len(plan.picks))
        return True

    async def _load_rules(self) -> list[ScheduleRule]:
        """Load ALL active rules for this station across all time windows.

        Station-specific rules take priority. If ANY exist for this station,
        global rules (station_id IS NULL) are ignored entirely.

        NOTE: Hour/day filtering is intentionally removed here. When filling a
        7-day queue window we need all daypart rules so _fill_content_time_aware
        can distribute content correctly across morning/afternoon/evening slots.
        """
        result = await self.db.execute(
            select(ScheduleRule)
            .where(
                ScheduleRule.is_active == True,
                ScheduleRule.station_id == self.station_id,
            )
            .order_by(ScheduleRule.priority.desc())
        )
        station_rules = list(result.scalars().all())

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
            )
            .order_by(ScheduleRule.priority.desc())
        )
        return list(result.scalars().all())

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

    @staticmethod
    def _rule_covers(rule: ScheduleRule, local: datetime) -> bool:
        """Whether a rule's hours and days include this station-local moment."""
        days = (rule.days_of_week or "0,1,2,3,4,5,6").split(",")
        if str(local.weekday()) not in days:
            return False
        start = rule.hour_start or 0
        end = 24 if rule.hour_end is None else rule.hour_end
        if start <= end:
            return start <= local.hour < end
        return local.hour >= start or local.hour < end  # range past midnight (22-2)

    async def _spread_rule_items(
        self, rules: list[ScheduleRule], fill_start_pos: int, queued_seconds: float,
    ) -> None:
        """Weave rotation and interval rule items through the fill.

        Rotation (songs_between=N): one item after every N filled songs.
        Interval (interval_minutes=N): one item each time N minutes of air time pass.
        Both only while the rule's hours and days cover the estimated air time
        (station local). The fill's entries (positions from fill_start_pos) are
        renumbered so the items sit between them.
        """
        spread = [
            r for r in rules
            if (r.rule_type == "rotation" and (r.songs_between or 0) > 0)
            or (r.rule_type == "interval" and (r.interval_minutes or 0) > 0)
        ]
        pools: dict = {}
        for rule in spread:
            assets = list(await self._find_assets_for_rule(rule))
            if assets:
                pools[rule.id] = assets
        spread = [r for r in spread if r.id in pools]
        if not spread:
            return

        await self.db.flush()
        rows = (await self.db.execute(
            select(QueueEntry.id, QueueEntry.position, func.coalesce(Asset.duration, DEFAULT_DURATION))
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.position >= fill_start_pos,
            )
            .order_by(QueueEntry.position)
        )).all()
        if not rows:
            return

        tz_name = (await self.db.execute(
            select(Station.timezone).where(Station.id == self.station_id)
        )).scalar()
        try:
            zone = ZoneInfo(tz_name or "UTC")
        except Exception:
            zone = timezone.utc
        fill_start_at = datetime.now(timezone.utc) + timedelta(seconds=queued_seconds)

        songs_since = {r.id: 0 for r in spread}
        next_due = {r.id: (r.interval_minutes or 0) * 60.0 for r in spread}
        used = {r.id: 0 for r in spread}
        elapsed = 0.0
        merged: list = []  # (entry_id, old_position) for fill entries, Asset for new items
        for entry_id, position, duration in rows:
            merged.append((entry_id, position))
            elapsed += float(duration)
            for rule in spread:
                if rule.rule_type == "rotation":
                    songs_since[rule.id] += 1
                    if songs_since[rule.id] < rule.songs_between:
                        continue
                    songs_since[rule.id] = 0
                else:
                    if elapsed < next_due[rule.id]:
                        continue
                    while next_due[rule.id] <= elapsed:
                        next_due[rule.id] += rule.interval_minutes * 60.0
                local = (fill_start_at + timedelta(seconds=elapsed)).astimezone(zone)
                if not self._rule_covers(rule, local):
                    continue
                pool = pools[rule.id]
                asset = pool[used[rule.id] % len(pool)]
                used[rule.id] += 1
                merged.append(asset)
                elapsed += float(asset.duration or DEFAULT_DURATION)

        if not any(used.values()):
            return
        moves = []
        for offset, item in enumerate(merged):
            position = fill_start_pos + offset
            if isinstance(item, Asset):
                await self._enqueue_asset(item, position)
            elif item[1] != position:
                moves.append({"b_id": item[0], "b_pos": position})
        if moves:
            table = QueueEntry.__table__
            await self.db.execute(
                update(table).where(table.c.id == bindparam("b_id")).values(position=bindparam("b_pos")),
                moves,
            )
        self.max_pos = max(self.max_pos, fill_start_pos + len(merged) - 1)
        logger.info("Rule items woven into the fill: %s", {r.name: used[r.id] for r in spread})

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
            query = query.where(in_category(rule.category), not_placeholder())
        else:
            query = query.where(fit_for_random_pick())
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

    async def _fill_content_time_aware(
        self,
        shortfall: float,
        rules: list[ScheduleRule],
    ) -> None:
        """Distribute the content shortfall across daypart rules proportionally.

        For each hour in the next 7 days we find the highest-priority daypart
        rule whose hour_start/hour_end range and days_of_week cover that slot.
        The shortfall is then divided in proportion to how many hours each rule
        "owns", so morning/afternoon/evening content is correctly represented in
        the queue even though we're filling days in advance.

        Falls back to plain music fill when no daypart rules exist.
        """
        daypart_rules = [r for r in rules if r.rule_type == "daypart"]
        if not daypart_rules:
            fill_type, fill_cat = self._get_fill_spec(rules)
            await self._fill_content(shortfall, asset_type=fill_type, category=fill_cat)
            return

        now = datetime.now(timezone.utc)
        total_hours = 168  # 7-day window

        # Count how many hours in the window each daypart rule "owns".
        # Rules are already sorted by priority desc so the first match wins.
        rule_hours: list[float] = [0.0] * len(daypart_rules)
        uncovered_hours = 0

        for h in range(total_hours):
            slot = now + timedelta(hours=h)
            slot_hour = slot.hour
            slot_weekday = slot.weekday()
            matched = False
            for i, rule in enumerate(daypart_rules):
                allowed_days = (rule.days_of_week or "0,1,2,3,4,5,6").split(",")
                if str(slot_weekday) not in allowed_days:
                    continue
                if rule.hour_start <= slot_hour < rule.hour_end:
                    rule_hours[i] += 1.0
                    matched = True
                    break
            if not matched:
                uncovered_hours += 1

        total_covered = sum(rule_hours)
        logger.info(
            "Time-aware fill: %.1fs shortfall across %d daypart rules "
            "(%d/%d hours covered in 7-day window)",
            shortfall, len(daypart_rules), int(total_covered), total_hours,
        )

        if total_covered == 0:
            # No rules match any slot in the window — plain fallback
            fill_type, fill_cat = self._get_fill_spec(rules)
            await self._fill_content(shortfall, asset_type=fill_type, category=fill_cat)
            return

        for i, rule in enumerate(daypart_rules):
            if rule_hours[i] <= 0:
                continue
            weight = rule_hours[i] / total_covered
            bucket_seconds = shortfall * weight
            logger.info(
                "  Rule '%s' (%s / %s): owns %d hrs (%.1f%%) → filling %.1fs",
                rule.name, rule.asset_type, rule.category or "any",
                int(rule_hours[i]), weight * 100, bucket_seconds,
            )
            await self._fill_content(bucket_seconds, asset_type=rule.asset_type, category=rule.category)

        # Any uncovered hours get a plain music fill
        if uncovered_hours > 0 and total_hours > 0:
            gap_seconds = shortfall * (uncovered_hours / total_hours)
            logger.info("  Gap (uncovered %d hrs): filling %.1fs with music", uncovered_hours, gap_seconds)
            await self._fill_content(gap_seconds, asset_type="music", category=None)

    async def _fill_content(
        self,
        shortfall: float,
        asset_type: str = "music",
        category: str | None = None,
    ) -> None:
        """Fill remaining queue with random content of the given type/category.

        Commits in batches of BATCH_SIZE to avoid overwhelming the database
        with a single massive flush. Recycles songs (allows repeats) when the
        library is exhausted before reaching the target duration.
        """
        BATCH_SIZE = 200
        logger.info("Filling %.1fs: type=%s category=%s", shortfall, asset_type, category)

        filled = 0.0
        passes = 0
        max_passes = 10  # safety limit to avoid infinite loop

        while filled < shortfall and passes < max_passes:
            passes += 1

            query = select(Asset).where(Asset.asset_type == asset_type)
            if category:
                query = query.where(in_category(category), not_placeholder())
            else:
                query = query.where(fit_for_random_pick())
            # Only exclude on first pass; allow repeats on subsequent passes
            if passes == 1 and self.exclude_ids:
                query = query.where(Asset.id.notin_(self.exclude_ids))
            elif passes == 2 and self.queued_ids:
                # Second pass: only exclude currently queued (allow recent replays)
                query = query.where(Asset.id.notin_(self.queued_ids))
            # passes >= 3: no exclusion, allow full recycling
            query = self._apply_oldies_filter(query)
            query = query.order_by(func.random())
            result = await self.db.execute(query)
            candidates = result.scalars().all()

            if not candidates:
                logger.warning("No %s/%s candidates on pass %d", asset_type, category, passes)
                if passes >= 3:
                    break  # truly no assets available
                continue  # try next pass with fewer exclusions

            batch_count = 0
            for asset in candidates:
                if filled >= shortfall:
                    break
                dur = asset.duration or DEFAULT_DURATION
                self.max_pos += 1
                await self._enqueue_asset(asset, self.max_pos)
                filled += dur
                batch_count += 1

                # Commit in batches to avoid huge single-transaction flushes
                if batch_count % BATCH_SIZE == 0:
                    await self.db.flush()
                    await self.db.commit()
                    logger.info("Committed batch of %d entries (%.1fs filled so far)", BATCH_SIZE, filled)

            # Flush remaining entries from this pass
            if batch_count % BATCH_SIZE != 0:
                await self.db.flush()
                await self.db.commit()

            logger.info("Pass %d: filled %.1fs from %d candidates (%d used)", passes, filled, len(candidates), batch_count)

            if filled >= shortfall:
                break

        logger.info("Total filled %.1fs of %s content in %d passes", filled, asset_type, passes)

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
            .where(QueueEntry.station_id == self.station_id, QueueEntry.status.in_(["pending", "playing"]))
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
        from app.services.spot_schedule_service import default_timing

        from app.services.weather_spot_service import station_announcement_zone

        station_timing = self.automation_config.get("spot_timing")
        # Each station announces its own time (Jerusalem said New York's 1 AM at 8 AM)
        zone = await station_announcement_zone(self.db, self.station_id)

        def timing_for(at: datetime) -> str:
            # Station default: on the second at :00, after the song otherwise. The
            # station's own minute: in a half-hour zone (India) UTC :30 is local :00.
            return default_timing(at.astimezone(zone).minute, station_timing)

        # Time and weather queued before timing_mode existed had none, which counted as
        # "on the second", so a :30 weather cut the song. Give them the station default.
        undecided = await self.db.execute(
            select(QueueEntry)
            .join(Asset, Asset.id == QueueEntry.asset_id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.timing_mode.is_(None),
                or_(QueueEntry.source.is_(None), QueueEntry.source != "ad_slot"),
                Asset.category.in_(ONE_MOMENT_CATEGORIES),
            )
        )
        for entry in undecided.scalars().all():
            at = entry.preempt_at if entry.preempt_at.tzinfo else entry.preempt_at.replace(tzinfo=timezone.utc)
            entry.timing_mode = timing_for(at)

        # ElevenLabs is optional: uploaded time clips and weather spots are used
        # when they exist (get_or_create_weather_spot_assets returns None for
        # whatever is missing when no TTS key is set).
        if not settings.supabase_storage_enabled:
            logger.info("Skipping hourly announcements: storage not configured")
            return

        from app.services.weather_spot_service import (
            get_or_create_weather_spot_assets,
            slot_key_for,
            station_weather_region,
        )

        now = datetime.now(timezone.utc)
        await self._drop_wrong_hour_announcements(zone)
        # ...and plays its own city's weather
        region = station_weather_region(self.automation_config)
        await self._realign_weather_region(region)
        await self._realign_time_clips(region)

        # Check which exact slot times already have scheduled ANNOUNCEMENT entries.
        # CRITICAL: exclude ad_slot entries and compare exact minutes — the old code
        # bucketed every preempt (including :15/:30/:45 ad slots) to the top of the
        # hour, so once ads were scheduled days ahead, every hour looked "already
        # scheduled" and time/weather announcements silently stopped being created.
        result = await self.db.execute(
            select(QueueEntry.preempt_at)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                # A program block due at the same minute (blocks_keep_hourly) does not
                # count: the hour still gets its time and weather.
                or_(QueueEntry.source.is_(None), QueueEntry.source.notin_(("ad_slot", "block"))),
            )
        )

        def _norm(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.replace(second=0, microsecond=0)

        existing_preempts = {_norm(row[0]) for row in result.all() if row[0]}

        weather_interval = int(self.automation_config.get("weather_interval_minutes", 30) or 0)
        half_hour_weather = self.automation_config.get("weather_enabled") and weather_interval == 30

        # Schedule for the next 48 hours (weather data only reliable ~48h out;
        # future runs will extend as hours come within range)
        # No time check or weather inside Shabbos / Yom Tov: queued 48 h ahead, a
        # Saturday-morning time check came due during the blackout and cut the silence
        from app.services.spot_schedule_service import load_blackouts

        blackouts = await load_blackouts(self.db, self.station_id, now, now + timedelta(hours=49))

        def off_air(at: datetime) -> bool:
            return any(b.covers(at) for b in blackouts)

        hours_scheduled = 0
        for hour_boundary in local_hour_marks(now, zone, 48):
            half_boundary = hour_boundary + timedelta(minutes=30)
            need_hour = hour_boundary not in existing_preempts and not off_air(hour_boundary)
            need_half = (
                half_hour_weather
                and half_boundary > now
                and half_boundary.astimezone(zone).minute == 30
                and half_boundary not in existing_preempts
                and not off_air(half_boundary)
            )
            if not need_hour and not need_half:
                continue

            slot_key = slot_key_for(hour_boundary, zone)

            try:
                time_asset, weather_asset = await get_or_create_weather_spot_assets(self.db, slot_key, region)
            except Exception:
                logger.warning("Failed to generate weather for slot %s", slot_key, exc_info=True)
                continue

            if need_hour:
                assets_to_insert = [a for a in [time_asset, weather_asset] if a is not None]
                for asset in assets_to_insert:
                    self.max_pos += 1
                    self.db.add(QueueEntry(
                        id=uuid.uuid4(),
                        station_id=self.station_id,
                        asset_id=asset.id,
                        position=self.max_pos,
                        status="pending",
                        preempt_at=hour_boundary,  # ALL entries get preempt_at so weather follows time
                        timing_mode=timing_for(hour_boundary),
                    ))
                if assets_to_insert:
                    hours_scheduled += 1
                    logger.info("Scheduled time+weather for %s (slot %s)", hour_boundary.isoformat(), slot_key)

            # Half-hour weather refresh (weather_interval_minutes == 30)
            if need_half and weather_asset is not None:
                self.max_pos += 1
                self.db.add(QueueEntry(
                    id=uuid.uuid4(),
                    station_id=self.station_id,
                    asset_id=weather_asset.id,
                    position=self.max_pos,
                    status="pending",
                    preempt_at=half_boundary,
                    timing_mode=timing_for(half_boundary),
                ))
                logger.info("Scheduled :30 weather for %s", half_boundary.isoformat())

        logger.info("Scheduled hourly announcements for %d hours", hours_scheduled)

    async def _schedule_ad_slots(self) -> None:
        """Schedule sponsor ad at :15, :30, :45 of every hour (soft preempt).

        Ads use preempt_at so they play when their clock time arrives — the
        playback engine waits for the current song to finish, then plays the
        ad before the next regular song.
        """
        legacy = self.automation_config.get("ad_slot_legacy_spot", False) is True
        await self._purge_placeholders(keep_ad_slots=legacy)

        # Stations with sponsor spot schedules: slots follow those schedules.
        from app.services.spot_schedule_service import reconcile_spot_slots

        reconciled = await reconcile_spot_slots(self.db, self.station_id)
        if reconciled is not None:
            self.max_pos = max(self.max_pos, reconciled["max_pos"])
            return

        # Legacy behaviour (no spot schedules yet): one placeholder spot in every slot.
        # Off unless a station sets automation_config.ad_slot_legacy_spot = true: the
        # placeholder ("KOL BRAMAH TEST SPONSOR") was airing on real stations.
        ad_title = self.automation_config.get("ad_slot_asset_title", "KOL BRAMAH TEST SPONSOR")
        result = await self.db.execute(
            select(Asset).where(Asset.title == ad_title).limit(1)
        )
        ad_asset = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if not legacy:
            return
        if not ad_asset:
            logger.warning("Ad asset '%s' not found", ad_title)
            return

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
            ad_minutes = self.automation_config.get("ad_slot_minutes", [15, 30, 45])
            for minute in ad_minutes:
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

    async def _purge_placeholders(self, keep_ad_slots: bool) -> None:
        """Take placeholder items (NEVER_AUTO_TITLES) out of the queue wherever an
        automatic pick put them; only entries not yet on air, never an operator's own."""
        ids = (await self.db.execute(select(Asset.id).where(~not_placeholder()))).scalars().all()
        if not ids:
            return
        # What an operator or listener chose stays; everything automatic goes.
        kept = ["manual", "request", "loop"]
        if keep_ad_slots:
            kept.append("ad_slot")
        result = await self.db.execute(
            delete(QueueEntry).where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.asset_id.in_(ids),
                QueueEntry.source.notin_(kept),
            )
        )
        if result.rowcount:
            logger.info("Station %s: removed %d placeholder entries", self.station_id, result.rowcount)

    # Backwards-compat alias
    async def _fill_random_music(self, shortfall: float) -> None:
        await self._fill_content(shortfall, asset_type="music", category=None)
