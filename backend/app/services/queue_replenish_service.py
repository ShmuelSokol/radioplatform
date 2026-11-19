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

from app.models.asset import (
    ONE_MOMENT_CATEGORIES,
    Asset,
    fit_for_random_pick,
    in_category,
    not_contract_audio,
    not_holiday_only,
    not_placeholder,
)
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.schedule_rule import ScheduleRule
from app.models.song_request import SongRequest
from app.models.sponsor import Sponsor
from app.models.station import Station

logger = logging.getLogger(__name__)

# A queue filled from playlists is topped up only once it is this far below
# its length (or a quarter of a short one); see _fill_from_playlists.
TOP_UP_SLACK_SECONDS = 20 * 60.0

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
        # Where each item last stands in the queue (its highest position), and
        # what aired in the last two hours: used when the list is used up
        self.last_place: dict = {}
        self.recent_ids: set = set()
        # A redraw (playlist fill): the automatic songs to be replaced are only
        # left out of every count while the new ones are picked, then old and
        # new are swapped in one short transaction (_fill_from_playlists)
        self.replace_all = False  # set by the caller: redraw everything
        self.keep_next: int | None = None  # songs a redraw keeps (None: KEEP_NEXT)
        self.replacing: list = []
        self.cursor_back: dict = {}
        self.replaced = 0
        # Station IDs between items in the rule fill (set up on first use)
        self._sprinkle = None
        # Holiday items in place of ordinary ones (set up on first use)
        self._holiday = None

    async def _load_automation_config(self) -> None:
        """Load the station's automation_config JSONB."""
        result = await self.db.execute(
            select(Station.automation_config).where(Station.id == self.station_id)
        )
        self.automation_config = result.scalar() or {}

    async def replenish(self) -> None:
        """Fill the queue (`_replenish`), then take out waiting station IDs that now
        sit next to a break or timed item (station_id_guard.drop_misplaced)."""
        await self._replenish()
        from app.services.station_id_guard import drop_misplaced, pull_late_id

        try:
            changed = await drop_misplaced(self.db, self.station_id)
            # and the next ID forward when moves and pass-overs left it late (12-20 min apart)
            from app.services.playlist_generator_service import id_settings_for

            if id_settings_for(getattr(self, "automation_config", None)).enabled:  # no extra read when IDs are off
                changed += await pull_late_id(self.db, self.station_id)
            if changed:
                await self.db.commit()
        except Exception:
            logger.exception("Station %s: misplaced station IDs could not be tidied", self.station_id)
            await self.db.rollback()
        from app.services.holiday_music import space_out

        try:
            if await space_out(self.db, self.station_id):
                await self.db.commit()
        except Exception:
            logger.exception("Station %s: holiday items could not be spaced out", self.station_id)
            await self.db.rollback()

    async def _replenish(self) -> None:
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
            KEEP_NEXT,
            automatic_songs,
            drop_dated_clips,
            realign_before_block_change,
            songs_to_replace,
            uses_block_playlists,
        )

        # A weather report or time check queued as an ordinary song is out of date
        await drop_dated_clips(self.db, self.station_id)

        # A station whose schedule blocks have playlists fills from them, a few hours
        # ahead, whether or not "Generate from sequences" was switched on. A week of
        # ordinary fill left from before is replaced, or the blocks would wait for it.
        generation = settings_for(self.automation_config)
        # Songs to be drawn again stay in the queue until the new ones are picked
        # (see `replacing`), so it never runs out of songs in between and nothing
        # is locked while picking
        redraw = self.replace_all
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
                redraw = True
                logger.info("Station %s now fills from its schedule blocks: old fill replaced", self.station_id)
        if by_schedule or (generation.enabled and await uses_block_playlists(self.db, self.station_id)):
            # Shortly before a block's playlist takes over, draw the songs again from
            # the real time on air so the block starts on time, not early
            station = await self._station()
            if await realign_before_block_change(self.db, station, datetime.now(timezone.utc), remove=False):
                redraw = True
        if redraw and generation.enabled:
            self.replacing, self.cursor_back = await songs_to_replace(
                self.db, self.station_id, keep_next=KEEP_NEXT if self.keep_next is None else self.keep_next
            )
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
        # "Hourly Station ID" (automation_config.hourly_station_id): the station ID
        # jingle airs in the same hh:00 group, after the time check and weather.
        wants_id = bool(self.automation_config.get("hourly_station_id"))
        if keep_hourly and (wants_hourly or wants_id):
            await self._schedule_top_of_hour(wants_hourly, wants_id)
            await self.db.flush()

        blocks_result = None
        try:
            blocks_result = await reconcile_block_entries(self.db, self.station_id)
        except Exception:
            logger.exception("Program blocks could not be queued for station %s", self.station_id)
        if blocks_result is not None:
            self.max_pos = max(self.max_pos, blocks_result["max_pos"])
        if blocks_result is None and (wants_hourly or wants_id) and not keep_hourly:
            await self._schedule_top_of_hour(wants_hourly, wants_id)

        # Sefiras HaOmer: the day's count in each hh:00 group, after the time check
        try:
            await self._schedule_omer_counts()
        except Exception:
            logger.exception("Omer count could not be queued for station %s", self.station_id)

        await self._schedule_ad_slots()
        if self.automation_config.get("hard_start", True) is False:
            # HS off: the time checks, block items and ad slots just written (or put
            # back to their planned timing) must not stay hard until the scheduler's
            # next pass, or one due meanwhile would cut the song (wiki/station-modes.md)
            from app.services.station_modes import soften_hard_items

            station = await self._station()
            await soften_hard_items(self.db, station)
        await self.db.commit()

        # Playlist generation: fill a few hours ahead from the schedule blocks'
        # playlist sequences instead of the rule-based 7-day fill.
        if generation.enabled:
            await self._generate_or_fill(generation, total_seconds)
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
            select(QueueEntry.asset_id, QueueEntry.position).where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status.in_(["pending", "playing"]),
            )
        )
        queued = result.all()
        self.queued_ids = {row[0] for row in queued}
        for asset_id, position in queued:
            if position is not None and position > self.last_place.get(asset_id, -1):
                self.last_place[asset_id] = position

        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await self.db.execute(
            select(PlayLog.asset_id).where(
                PlayLog.station_id == self.station_id,
                PlayLog.start_utc >= cutoff,
                PlayLog.asset_id.isnot(None),
            )
        )
        recent_ids = {row[0] for row in result.all()}
        self.recent_ids = recent_ids
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

    async def _generate_or_fill(self, generation, total_seconds: float) -> None:
        if await self._fill_from_playlists(generation, total_seconds):
            return
        # Nothing could be generated (no block with a sequence): keep the
        # station on the air with the ordinary fill, but only as far ahead
        # as generation looks, so sequences take over once they exist.
        if self.replacing:
            # A redraw with nothing to draw from: the old songs go, the ordinary fill follows
            total_seconds -= await self._delete_replaced()
        shortfall = generation.hours * 3600 - total_seconds
        if shortfall > 0:
            self.queued_ids = set()
            self.exclude_ids = set()
            await self._fill_content(shortfall, asset_type="music", category=None)

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
        from app.services.weather_spot_service import clip_minute, find_time_clip_asset

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
        best: dict[tuple[str, int], Asset | None] = {}
        for entry, asset in rows.all():
            meta = asset.metadata_extra if isinstance(asset.metadata_extra, dict) else {}
            hour = meta.get("time_of_day")
            if not hour:
                continue  # a generated per-slot clip, not an uploaded set
            key = (hour, clip_minute(asset))
            if key not in best:
                best[key] = await find_time_clip_asset(self.db, int(hour), region, key[1])
            clip = best[key]
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
        from app.services.weather_spot_service import clip_hour, clip_minute

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
            local = at.astimezone(zone)
            if hour is not None and (hour != local.hour or clip_minute(asset) != local.minute):
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
                    *self._not_replaced(),
                )
                .order_by(QueueEntry.position)
            )
        ).all()

        def aware(dt: datetime) -> datetime:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        start = now
        timed: list = []
        waiting: list[tuple] = []
        for entry, duration, meta in rows:
            seconds = on_air_seconds(duration, meta if isinstance(meta, dict) else None)
            if entry.status == "playing":
                if entry.started_at is not None:
                    start = max(start, aware(entry.started_at) + timedelta(seconds=seconds))
            elif entry.preempt_at is not None:
                timed.append(TimedItem(max(aware(entry.preempt_at), now), seconds, not is_soft_timed(entry)))
            else:
                waiting.append((entry.asset_id, seconds))
        clock = Clock(start, timed)
        # When each waiting item is expected to start (station IDs count from the last one)
        self.queue_starts = []
        for asset_id, seconds in waiting:
            self.queue_starts.append((asset_id, clock.start()))
            clock.play(seconds)
        fill_start = clock.start()
        # When the last timed item before the new songs ends (station IDs keep clear of it)
        self.timed_ended = clock.last_timed_end
        return fill_start, clock.timed

    async def _station(self) -> Station:
        """The station, read once per session. Reading it again for each step
        of a top-up was two round trips a time (the station and its channels),
        five times over. Where its settings are written it is still read afresh."""
        station = await self.db.get(Station, self.station_id)
        if station is None:
            raise ValueError(f"Station {self.station_id} not found")
        return station

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
        if not self.replacing and target - queued_seconds < min(TOP_UP_SLACK_SECONDS, target / 4):
            # Nearly full: the next songs are drawn in one go once a few have
            # played. Drawing one song after every song change cost about 14
            # database round trips each time, inside the song change.
            return True
        station = await self._station()
        pending = (
            await self.db.execute(
                select(QueueEntry.asset_id, Asset.artist, Asset.asset_type, QueueEntry.preempt_at)
                .join(Asset, QueueEntry.asset_id == Asset.id)
                .where(
                    QueueEntry.station_id == self.station_id,
                    QueueEntry.status.in_(["pending", "playing"]),
                    *self._not_replaced(),
                )
                .order_by(QueueEntry.position)
            )
        ).all()
        self.queued_ids = {row[0] for row in pending}
        # The new songs follow the last ordinary item waiting (a station ID only follows a song)
        ordinary = [row for row in pending if row[3] is None]
        after_type = (ordinary[-1][2] if ordinary else None) or "music"
        config = dict(station.automation_config or {})
        saved = dict(config.get(CONFIG_KEY) or {})
        cursor = dict(saved.get("cursor") or {})
        # Songs being replaced were drawn from their blocks' playlists: each
        # block's place goes back by them, so the redraw carries on from the same step
        for block_id, n in self.cursor_back.items():
            if block_id in cursor:
                cursor[block_id] = int(cursor[block_id]) - n
        plan = await generate(
            self.db,
            station,
            fill_start,
            (target - queued_seconds) / 3600,
            artist_distance=generation.artist_distance,
            min_vote=generation.min_vote,
            cursor=cursor,
            recent_artists=[row[1] for row in pending][-20:],
            busy_ids=set(self.queued_ids),
            queued=[row[0] for row in pending],
            timed=timed,
            timed_ended=getattr(self, "timed_ended", None),
            after_type=after_type,
            queue_starts=getattr(self, "queue_starts", None),
        )
        if not plan.picks:
            logger.info("Playlist generation produced nothing: %s", "; ".join(plan.notes))
            return False
        # Everything above only read. End that transaction, so the swap below
        # (old songs out, new ones in) is one short transaction of its own.
        await self.db.commit()
        station = (
            await self.db.execute(select(Station).where(Station.id == self.station_id))
        ).scalar_one()
        config = dict(station.automation_config or {})
        saved = dict(config.get(CONFIG_KEY) or {})
        await self._delete_replaced()
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

    def _not_replaced(self) -> tuple:
        """Leaves the songs a redraw is replacing out of a queue read."""
        return (QueueEntry.id.notin_(self.replacing),) if self.replacing else ()

    async def _delete_replaced(self) -> float:
        """Remove the songs a redraw replaces. One that started meanwhile, or
        that the playout engine now holds as its next track, stays. Returns the
        seconds removed. Does not commit."""
        if not self.replacing:
            return 0.0
        from app.services.playout_engine import held_entry_ids

        conditions = [QueueEntry.id.in_(self.replacing), QueueEntry.status == "pending"]
        held = held_entry_ids(self.station_id)
        if held:
            conditions.append(QueueEntry.id.notin_(held))
        seconds = (
            await self.db.execute(
                select(func.coalesce(func.sum(func.coalesce(Asset.duration, DEFAULT_DURATION)), 0))
                .select_from(QueueEntry)
                .join(Asset, QueueEntry.asset_id == Asset.id)
                .where(*conditions)
            )
        ).scalar() or 0
        result = await self.db.execute(
            delete(QueueEntry).where(*conditions).execution_options(synchronize_session=False)
        )
        self.replaced = result.rowcount or 0
        self.replacing = []
        self.cursor_back = {}
        return float(seconds)

    async def _load_rules(self) -> list[ScheduleRule]:
        """Load ALL active rules for this station across all time windows
        (see rules_for_station).

        NOTE: Hour/day filtering is intentionally removed here. When filling a
        7-day queue window we need all daypart rules so _fill_content_time_aware
        can distribute content correctly across morning/afternoon/evening slots.
        """
        return await rules_for_station(self.db, self.station_id)

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
        return rule_covers(rule, local)

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
        """This station's own limits on a draw: never an item it was told not to play
        ("Not on this station"), and with oldies_only config only old release dates."""
        from app.services.station_blocks import blocked_asset_ids

        query = query.where(Asset.id.notin_(blocked_asset_ids(self.station_id)))
        if self.automation_config.get("oldies_only"):
            min_years = self.automation_config.get("oldies_min_years", 10)
            cutoff = date.today() - timedelta(days=min_years * 365)
            query = query.where(Asset.release_date.isnot(None), Asset.release_date <= cutoff)
        return query

    async def _find_assets_for_rule(self, rule: ScheduleRule) -> Sequence[Asset]:
        query = select(Asset).where(Asset.asset_type == rule.asset_type)
        if rule.category:
            query = query.where(in_category(rule.category), not_placeholder(), not_contract_audio(), not_holiday_only())
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
        self.last_place[asset.id] = max(position, self.last_place.get(asset.id, -1))
        self.max_pos = max(self.max_pos, position)
        logger.debug("Enqueued %s '%s' at position %d", asset.asset_type, asset.title, position)

    async def _insert_sponsors(self, shortfall: float) -> None:
        # Spots mode off (control room): no sponsorship goes in the queue
        if (self.automation_config or {}).get("spots_on", True) is False:
            return
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
                query = query.where(in_category(category), not_placeholder(), not_contract_audio(), not_holiday_only())
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
            in_turn = passes >= 3
            # In turn, the rounds left are drawn as one run (a list is used at
            # most max_passes times a fill, so one song never fills a week)
            run = len(candidates) * (max_passes - passes + 1)
            for asset in self._in_turn(candidates) if in_turn else candidates:
                if filled >= shortfall or (in_turn and batch_count >= run):
                    break
                asset = await self._holiday_instead(asset)
                dur = asset.duration or DEFAULT_DURATION
                self.max_pos += 1
                await self._enqueue_asset(asset, self.max_pos)
                filled += dur
                batch_count += 1
                filled += await self._station_id_after(asset)

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

            if filled >= shortfall or in_turn:
                break

        logger.info("Total filled %.1fs of %s content in %d passes", filled, asset_type, passes)

    async def _holiday_instead(self, asset: Asset) -> Asset:
        """Rule fill (All Day Stories): before and during a holiday, now and then an
        item from the holiday's list instead of this one (same kind: a story or
        shiur for a story, a song for a song). See services/holiday_music.py."""
        if self._holiday is False:
            return asset
        from app.services.holiday_music import HolidayMix
        from app.services.playlist_generator_service import on_air_seconds

        if self._holiday is None:
            self._holiday = False
            now = datetime.now(timezone.utc)
            start, _ = await self._air_timeline(now)
            mix = await HolidayMix.for_station(self.db, await self._station(), start)
            if mix is None:
                return asset
            self._holiday = (mix, [start])
        mix, at = self._holiday
        got = mix.pick(asset.asset_type, at[0])
        chosen = asset
        if got is not None:
            chosen = await self.db.get(Asset, got[0].id) or asset
        meta = chosen.metadata_extra if isinstance(chosen.metadata_extra, dict) else None
        at[0] += timedelta(seconds=on_air_seconds(chosen.duration, meta))
        return chosen

    async def _station_id_after(self, asset: Asset) -> float:
        """Rule fill (All Day Stories): after each item, a station ID when one is
        due (automation_config.station_ids, the same rule as the playlist fill).
        Returns the seconds added."""
        from app.services.playlist_generator_service import (
            Clock,
            StationIdSprinkler,
            _Library,
            id_settings_for,
            on_air_seconds,
        )

        if self._sprinkle is None:
            ids = id_settings_for(self.automation_config)
            self._sprinkle = False
            if not ids.enabled:
                return 0.0
            from app.services.spot_schedule_service import load_blackouts
            from app.services.weather_spot_service import station_announcement_zone

            now = datetime.now(timezone.utc)
            # The queue as it stands before this item: its end and the timed items after it
            start, timed = await self._air_timeline(now)
            clock = Clock(start, timed)
            clock.last_timed_end = getattr(self, "timed_ended", None)
            library = _Library(self.db, self.station_id, set(), queued=None, start=start)
            blackouts = await load_blackouts(self.db, self.station_id, now, now + timedelta(days=8))
            zone = await station_announcement_zone(self.db, self.station_id)
            sprinkler = StationIdSprinkler(ids, library, blackouts, zone)
            # The item just queued is already part of the walk above (autoflush)
            await sprinkler.prepare(start, getattr(self, "queue_starts", []))
            self._sprinkle = (sprinkler, clock)
            got = await sprinkler.after(clock, asset.asset_type)
        elif self._sprinkle is False:
            return 0.0
        else:
            sprinkler, clock = self._sprinkle
            clock.play(on_air_seconds(asset.duration, asset.metadata_extra if isinstance(asset.metadata_extra, dict) else None))
            got = await sprinkler.after(clock, asset.asset_type)
        if got is None:
            return 0.0
        row, _ = got
        jingle = await self.db.get(Asset, row.id)
        if jingle is None:
            return 0.0
        self.max_pos += 1
        await self._enqueue_asset(jingle, self.max_pos)
        return float(jingle.duration or 10.0)

    def _in_turn(self, candidates):
        """The list has been used up and items come round again: yield the one
        whose last place in the queue is furthest back, chosen at random among
        the most rested quarter, for as long as the caller asks.

        A fresh random order for each round put the end of one round next to
        the start of the next (All Day Stories had one story twice 11 minutes
        apart on 2026-11-18), and a top-up of one or two items could pick the
        story queued a moment ago.
        """
        import random

        def rest(asset):
            place = self.last_place.get(asset.id)
            if place is not None:
                return (2, place)
            return (1, 0) if asset.id in self.recent_ids else (0, 0)

        order = sorted(candidates, key=lambda a: (rest(a), random.random()))
        while order:
            asset = order.pop(random.randrange(max(1, len(order) // 4)))
            yield asset
            order.append(asset)

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

    async def _schedule_top_of_hour(self, time_and_weather: bool, station_id: bool) -> None:
        if time_and_weather:
            await self._schedule_hourly_announcements()
        if station_id:
            await self._schedule_hourly_station_ids()

    async def _schedule_hourly_station_ids(self) -> None:
        """Queue the station ID jingle (jingle / hourly_id) at each of the station's own
        hh:00 for the next 48 hours, in the same timed group as the time check and
        weather and after them (the time check stays on the second).

        The "Hourly Station ID" switch was read only by the playlist-template path,
        which nothing calls, so it did nothing (V1-79). A jingle whose title names the
        hour ("3:00 PM") is used for that hour, otherwise any station ID jingle; with
        no jingle in the library nothing is queued. None inside Shabbos / Yom Tov."""
        from app.models.asset import not_placeholder
        from app.services.spot_schedule_service import load_blackouts
        from app.services.weather_spot_service import station_announcement_zone

        jingles = list((await self.db.execute(
            select(Asset)
            .where(Asset.asset_type == "jingle", func.lower(Asset.category) == "hourly_id", not_placeholder())
            .order_by(Asset.title)
        )).scalars().all())
        if not jingles:
            return
        zone = await station_announcement_zone(self.db, self.station_id)
        now = datetime.now(timezone.utc)
        queued = await self.db.execute(
            select(QueueEntry.preempt_at).where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.asset_id.in_([j.id for j in jingles]),
            )
        )
        have = {
            (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).replace(second=0, microsecond=0)
            for (at,) in queued.all()
        }
        blackouts = await load_blackouts(self.db, self.station_id, now, now + timedelta(hours=49))

        def for_hour(hour: int) -> Asset:
            label = f"{12 if hour % 12 == 0 else hour % 12}:00 {'AM' if hour < 12 else 'PM'}"
            for j in jingles:
                title = j.title or ""
                at = title.find(label)
                # "1:00 PM" must not match "11:00 PM"
                if at >= 0 and (at == 0 or not title[at - 1].isdigit()):
                    return j
            return jingles[0]

        for mark in local_hour_marks(now, zone, 48):
            if mark in have or any(b.covers(mark) for b in blackouts):
                continue
            self.max_pos += 1
            self.db.add(QueueEntry(
                id=uuid.uuid4(),
                station_id=self.station_id,
                asset_id=for_hour(mark.astimezone(zone).hour).id,
                position=self.max_pos,
                status="pending",
                preempt_at=mark,
                # Soft: it follows the time check and weather of the same hh:00 group
                # (by position) and never cuts anything; hard, it would start ~45 s
                # after its time and the air watch would report it late.
                timing_mode="soft",
            ))

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
            select(QueueEntry.preempt_at, Asset.category)
            .join(Asset, Asset.id == QueueEntry.asset_id)
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

        # The hour's Omer count (queued by its own pass) does not stand for its time check
        existing_rows = [(_norm(at), category) for at, category in result.all() if at and category != "omer_count"]
        existing_preempts = {at for at, _ in existing_rows}
        # A half hour that already has its weather can still lack its time check
        # (switched on later, or the clip recorded later)
        timed_checks = {at for at, category in existing_rows if category == "time_announcement"}
        from app.services.weather_spot_service import find_time_clip_asset, half_hour_time_check

        half_time_on = half_hour_time_check(self.automation_config)
        half_clips: dict[int, Asset | None] = {}

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
        # The hour that has already begun still owes its :30. Only coming hours were
        # walked, and a :30 is made with its hour, so nothing made it when the hour's
        # own start was never a coming hour of a fill: after a Yom Tov longer than the
        # 48 h time and weather are queued ahead (nothing is filled during a blackout;
        # back at 9:01 PM, no 9:30 time check), or a start with an empty queue
        marks = local_hour_marks(now, zone, 48)
        begun = local_hour_marks(now - timedelta(hours=1), zone, 1)
        if begun and begun[0] <= now and begun[0] not in marks:
            marks.insert(0, begun[0])
        for hour_boundary in marks:
            half_boundary = hour_boundary + timedelta(minutes=30)
            need_hour = (
                hour_boundary > now and hour_boundary not in existing_preempts and not off_air(hour_boundary)
            )
            half_ok = (
                half_boundary > now
                and half_boundary.astimezone(zone).minute == 30
                and not off_air(half_boundary)
            )
            need_half = half_ok and half_hour_weather and half_boundary not in existing_preempts
            # The half-hour time check ("The time is 10:30 ..."), uploaded clips only
            half_clip = None
            if half_ok and half_time_on and half_boundary not in timed_checks:
                local_hour = half_boundary.astimezone(zone).hour
                if local_hour not in half_clips:
                    half_clips[local_hour] = await find_time_clip_asset(self.db, local_hour, region, 30)
                half_clip = half_clips[local_hour]
            if half_clip is not None:
                added = [self._timed_entry(half_clip, half_boundary, timing_for(half_boundary))]
                if need_half:
                    # its weather comes with it, right behind
                    need_half = False
                    weather = await self._half_hour_weather(slot_key_for(hour_boundary, zone), region)
                    if weather is not None:
                        added.append(self._timed_entry(weather, half_boundary, timing_for(half_boundary)))
                await self._put_first(half_boundary, added)
                logger.info("Scheduled :30 time check for %s", half_boundary.isoformat())
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
                # ALL entries get preempt_at so weather follows time
                added_hour = [
                    self._timed_entry(asset, hour_boundary, timing_for(hour_boundary))
                    for asset in assets_to_insert
                ]
                if assets_to_insert:
                    await self._put_first(hour_boundary, added_hour)
                    hours_scheduled += 1
                    logger.info("Scheduled time+weather for %s (slot %s)", hour_boundary.isoformat(), slot_key)

            # Half-hour weather refresh (weather_interval_minutes == 30)
            if need_half and weather_asset is not None:
                await self._put_first(
                    half_boundary, [self._timed_entry(weather_asset, half_boundary, timing_for(half_boundary))]
                )
                logger.info("Scheduled :30 weather for %s", half_boundary.isoformat())

        logger.info("Scheduled hourly announcements for %d hours", hours_scheduled)

    async def _schedule_omer_counts(self) -> None:
        """During Sefiras HaOmer, queue the count (services/omer_count.py) at each of the
        station's own hh:00 for the next 48 hours, right after that hour's time check
        and before its weather, timed like the time check. The next day's count from
        shekia (plus the station's switch minutes); days only, as the siddur. Nothing
        inside Shabbos / Yom Tov, nothing without the recorded clip, nothing with the
        station's setting off. Queued counts that no longer fit (setting changed, a
        clip recorded, a blackout added) are taken out or replaced."""
        from app.services import omer_count as omer
        from app.services.spot_schedule_service import default_timing, load_blackouts
        from app.services.weather_spot_service import announcement_zone

        now = datetime.now(timezone.utc)
        station = await self._station()
        zone = announcement_zone(station.timezone)
        today = now.astimezone(zone).date()
        in_season = any(omer.omer_day_on(today + timedelta(days=d)) for d in range(-1, 4))
        if not in_season:
            return

        setting = omer.omer_setting(self.automation_config)
        place = omer.station_place(station.latitude, station.longitude, zone.key)
        if place is None:
            logger.warning("Station %s has no place to count the Omer from", station.name)
        clips = await omer.load_clips(self.db) if setting.enabled and place is not None else {}
        clip_ids = {a.id for a in clips.values()}
        blackouts = await load_blackouts(self.db, self.station_id, now, now + timedelta(hours=49))
        station_timing = self.automation_config.get("spot_timing")

        wanted: dict[datetime, Asset] = {}
        if clips:
            for mark in local_hour_marks(now, zone, 48):
                if any(b.covers(mark) for b in blackouts):
                    continue
                said = omer.omer_at(mark, zone, place, setting.switch_minutes)
                if said is None:
                    continue
                clip = clips.get(said)
                if clip is not None:
                    wanted[mark] = clip

        queued = (await self.db.execute(
            select(QueueEntry)
            .join(Asset, Asset.id == QueueEntry.asset_id)
            .where(
                QueueEntry.station_id == self.station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                Asset.category == omer.CATEGORY,
            )
        )).scalars().all()
        have: set[datetime] = set()
        for entry in queued:
            at = omer.as_utc(entry.preempt_at).replace(second=0, microsecond=0)
            if at <= now:
                continue  # due now or on air: left alone
            if wanted.get(at) is not None and wanted[at].id == entry.asset_id and at not in have:
                have.add(at)
            else:
                await self.db.delete(entry)
        await self.db.flush()

        for mark, clip in sorted(wanted.items()):
            if mark in have:
                continue
            entry = self._timed_entry(clip, mark, default_timing(mark.astimezone(zone).minute, station_timing))
            await self._after_time_check(mark, entry)
        if clip_ids and wanted:
            logger.info("Omer count queued for %d hours on station %s", len(wanted) - len(have), station.name)

    async def _after_time_check(self, at: datetime, entry: QueueEntry) -> None:
        """Put `entry` right after the time check of the group due at `at` (first in the
        group when it has none). The group's positions are handed out again; positions
        outside the group are not touched."""
        await self.db.flush()
        group = (
            await self.db.execute(
                select(QueueEntry, Asset.category)
                .join(Asset, Asset.id == QueueEntry.asset_id)
                .where(
                    QueueEntry.station_id == self.station_id,
                    QueueEntry.status == "pending",
                    QueueEntry.preempt_at == at,
                )
                .order_by(QueueEntry.position)
            )
        ).all()
        others = [(e, c) for e, c in group if e.id != entry.id]
        if not others:
            return
        times = [e for e, c in others if c == "time_announcement"]
        rest = [e for e, c in others if c != "time_announcement"]
        order = times + [entry] + rest
        slots = sorted(e.position for e, _ in group)
        for e, position in zip(order, slots):
            e.position = position
        await self.db.flush()

    def _timed_entry(self, asset: Asset, at: datetime, timing_mode: str) -> QueueEntry:
        """A new automatic time or weather entry due at `at`, at the end of the queue."""
        self.max_pos += 1
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=self.station_id,
            asset_id=asset.id,
            position=self.max_pos,
            status="pending",
            preempt_at=at,
            timing_mode=timing_mode,
        )
        self.db.add(entry)
        return entry

    async def _half_hour_weather(self, slot_key: str, region: str) -> Asset | None:
        from app.services.weather_spot_service import get_or_create_weather_spot_assets

        try:
            _, weather = await get_or_create_weather_spot_assets(self.db, slot_key, region)
        except Exception:
            logger.warning("Failed to get weather for slot %s", slot_key, exc_info=True)
            return None
        return weather

    async def _put_first(self, at: datetime, added: list[QueueEntry]) -> None:
        """Make the new time check (and weather) play first in the group due at `at`.

        Entries due at the same minute play in position order. A block or sponsorship
        queued earlier for that minute has a lower position than a time check added
        now, so it would air before "The time is 10:30". The group's positions are
        handed out again: the new entries take the lowest, the others follow in
        their old order. Positions outside the group are not touched.
        """
        if not added:
            return
        await self.db.flush()
        ids = {e.id for e in added}
        group = (
            await self.db.execute(
                select(QueueEntry)
                .where(
                    QueueEntry.station_id == self.station_id,
                    QueueEntry.status == "pending",
                    QueueEntry.preempt_at == at,
                )
                .order_by(QueueEntry.position)
            )
        ).scalars().all()
        others = [e for e in group if e.id not in ids]
        if not others:
            return
        # The hour's Omer count stays right behind its time check (before the weather)
        omer_ids = set()
        if any(e.preempt_at is not None for e in others):
            from app.services.omer_count import CATEGORY as OMER

            omer_ids = set((await self.db.execute(
                select(QueueEntry.id).join(Asset, Asset.id == QueueEntry.asset_id).where(
                    QueueEntry.id.in_([e.id for e in others]), Asset.category == OMER
                )
            )).scalars().all())
        counts = [e for e in others if e.id in omer_ids]
        order = added[:1] + counts + added[1:] + [e for e in others if e.id not in omer_ids]
        slots = sorted(e.position for e in group)
        for entry, position in zip(order, slots):
            entry.position = position
        await self.db.flush()

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
        from app.services.station_blocks import blocked_asset_ids

        ids = (await self.db.execute(select(Asset.id).where(~not_placeholder()))).scalars().all()
        # and what this station alone was told not to play ("Not on this station")
        ids = list(ids) + list((await self.db.execute(blocked_asset_ids(self.station_id))).scalars().all())
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


async def rules_for_station(db: AsyncSession, station_id) -> list[ScheduleRule]:
    """The active rules that apply to one station, highest priority first.

    A station's own rules win: if it has any, the global rules (no station) are
    ignored entirely. A rule that belongs to another station never applies. Every
    place that judges a station by its rules reads them here (the fill, the warning
    on a queue move, the Rules preview): on 2026-11-17 a queue move on Kol B'ramah
    Main was warned about by the All Day Stories station's rule because that check
    read every station's rules.
    """
    own = list(
        (
            await db.execute(
                select(ScheduleRule)
                .where(ScheduleRule.is_active == True, ScheduleRule.station_id == station_id)  # noqa: E712
                .order_by(ScheduleRule.priority.desc())
            )
        ).scalars().all()
    )
    if own:
        return own
    return list(
        (
            await db.execute(
                select(ScheduleRule)
                .where(ScheduleRule.is_active == True, ScheduleRule.station_id.is_(None))  # noqa: E712
                .order_by(ScheduleRule.priority.desc())
            )
        ).scalars().all()
    )


def rule_covers(rule: ScheduleRule, local: datetime) -> bool:
    """Whether a rule's hours and days include this station-local moment."""
    days = (rule.days_of_week or "0,1,2,3,4,5,6").split(",")
    if str(local.weekday()) not in days:
        return False
    start = rule.hour_start or 0
    end = 24 if rule.hour_end is None else rule.hour_end
    if start <= end:
        return start <= local.hour < end
    return local.hour >= start or local.hour < end  # range past midnight (22-2)
