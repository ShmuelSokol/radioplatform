"""
Scheduler engine — background task that automatically advances playback based on schedules.
This runs continuously and updates now-playing state when blocks change or assets end.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.engine import async_session_factory
from app.models.asset import Asset, stored_analysis
from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.models.queue_entry import aired_from, resume_offset

logger = logging.getLogger(__name__)

# Longest wait between tries of a daily weather readout that could not be made
READOUT_RETRY_MAX_SECONDS = 30 * 60


async def _safe_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:
        logger.warning("Rollback after a station error failed: %s", exc)


class SchedulerEngine:
    """
    Background scheduler that:
    1. Monitors active stations
    2. Resolves which block should be playing
    3. Advances to next asset when current one ends
    4. Broadcasts updates via WebSocket
    """
    
    def __init__(self, check_interval: int = 3):
        self.check_interval = check_interval  # seconds
        self.running = False
        self._task: Optional[asyncio.Task] = None
        # When each station was last checked for a live show nobody ever heard (monotonic)
        self._unheard_checked: dict = {}
        # station id -> when its live show's host was first found silent (monotonic)
        self._host_gone_since: dict[str, float] = {}
        # Silence detection: station_id → datetime when silence first detected
        self._silence_start: dict[str, datetime] = {}
        # Block transition tracking: station_id → last active block ID
        # Stations already alerted for the current dead-air spell (one alert each)
        self._silence_alerted: set[str] = set()
        # Daily holiday extension check
        self._last_holiday_check: Optional[datetime] = None
        # Weather readout daily generation check: station_id → date string
        self._last_readout_check: dict[str, str] = {}
        # Failed readouts: station_id -> (date string, failures so far, monotonic time of next try)
        self._readout_retry: dict[str, tuple[str, int, float]] = {}
        # Precise advance timers: station_id → TimerHandle
        self._advance_timers: dict[str, asyncio.TimerHandle] = {}
        # station -> (due time, timer) for the next timed item (hard start or hard_by)
        self._timed_timers: dict[str, tuple[datetime, asyncio.TimerHandle]] = {}
        # station -> due time of the timed item pages were last told about (_announce_timed)
        self._timed_announced: dict[str, datetime] = {}
        # Leadership: only the process holding the lease advances queues.
        self._instance_id = uuid.uuid4().hex
        self._leader_until: Optional[datetime] = None
        # A pass is running (stop() lets it finish rather than cancel it mid-query)
        self._in_pass = False
        self._pass_done = asyncio.Event()
    
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
            # Let the pass in progress finish (the loop ends after it) before cancelling.
            # Cancelled in the middle of a database call, a pass could leave its
            # connection with a write transaction open, and the next schema change or
            # test teardown then waited on "database is locked".
            if self._in_pass:
                try:
                    await asyncio.wait_for(asyncio.shield(self._pass_done.wait()), timeout=self.STOP_GRACE_SECONDS)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            self._task.cancel()  # now asleep between passes (or a pass that hung)
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for timer in self._advance_timers.values():
            timer.cancel()
        self._advance_timers.clear()
        self._cancel_timed_timers()
        await self._release_lease()
        logger.info("Scheduler engine stopped")
    
    LEASE_SECONDS = 15
    STOP_GRACE_SECONDS = 5.0

    @property
    def is_leader(self) -> bool:
        return self._leader_until is not None and datetime.now(timezone.utc) < self._leader_until

    async def _hold_lease(self, db: AsyncSession) -> bool:
        """Take or renew the scheduler lease. False while another live process holds it."""
        from sqlalchemy import text
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.LEASE_SECONDS)
        params = {"me": self._instance_id, "now": now, "expires": expires}
        result = await db.execute(
            text(
                "UPDATE scheduler_leases SET holder = :me, expires_at = :expires "
                "WHERE name = 'scheduler' AND (holder = :me OR expires_at < :now)"
            ),
            params,
        )
        held = result.rowcount == 1
        if not held:
            result = await db.execute(
                text(
                    "INSERT INTO scheduler_leases (name, holder, expires_at) "
                    "VALUES ('scheduler', :me, :expires) ON CONFLICT (name) DO NOTHING"
                ),
                params,
            )
            held = result.rowcount == 1
        await db.commit()
        was_leader = self.is_leader
        self._leader_until = expires if held else None
        if held and not was_leader:
            logger.info("Scheduler %s took the lease", self._instance_id[:8])
        if not held and was_leader:
            logger.warning("Scheduler %s lost the lease", self._instance_id[:8])
            for timer in self._advance_timers.values():
                timer.cancel()
            self._advance_timers.clear()
            self._cancel_timed_timers()
        return held

    async def _release_lease(self):
        """Hand the lease over at shutdown so the next process takes it at once."""
        if self._leader_until is None:
            return
        self._leader_until = None
        try:
            from sqlalchemy import text
            async with async_session_factory() as db:
                await db.execute(
                    text("UPDATE scheduler_leases SET expires_at = :now WHERE name = 'scheduler' AND holder = :me"),
                    {"me": self._instance_id, "now": datetime.now(timezone.utc)},
                )
                await db.commit()
        except Exception as e:
            logger.warning("Could not release scheduler lease: %s", e)

    async def _run_loop(self):
        """Main scheduler loop."""
        while self.running:
            self._in_pass = True
            self._pass_done.clear()
            try:
                # A session of its own per pass, closed (and its transaction ended)
                # as soon as the pass is done. It used to come from an `async for`
                # over get_db() left with `break`, which keeps the generator
                # suspended until garbage collection: the session sat "idle in
                # transaction" holding locks on the tables it had read, and a
                # deploy's schema change queued behind it froze every station (see
                # wiki/streaming-architecture.md, "Deploys must never freeze").
                async with async_session_factory() as db:
                    if await self._hold_lease(db):  # False: another process runs the stations
                        await self._check_all_stations(db)
                        await self._maybe_extend_holidays(db)
                        await self._maybe_generate_weather_readouts(db)
                        await self._maybe_queue_weather_readouts(db)
                    await _safe_rollback(db)  # end the read transaction now
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
            finally:
                self._in_pass = False
                self._pass_done.set()

            await asyncio.sleep(self.check_interval)
    
    async def _maybe_extend_holidays(self, db: AsyncSession):
        """Once daily, check if holiday coverage needs extending for each station."""
        now = datetime.now(timezone.utc)
        if self._last_holiday_check and (now - self._last_holiday_check).total_seconds() < 86400:
            return

        self._last_holiday_check = now
        try:
            from app.services.station_service import auto_generate_holidays_for_station

            stmt = select(Station).where(
                Station.is_active == True,
                Station.latitude.isnot(None),
                Station.longitude.isnot(None),
            )
            result = await db.execute(stmt)
            stations = result.scalars().all()

            # Each station's own windows: looking across all stations let one
            # covered station hide another that had none (it then played through Shabbos)
            from app.services.station_service import window_covers_station
            windows = (await db.execute(
                select(HolidayWindow).where(
                    HolidayWindow.is_blackout == True,
                    HolidayWindow.end_datetime >= now,
                )
            )).scalars().all()
            next_week = now + timedelta(days=7)
            # Plain values: a commit below expires the loaded rows
            stations = [(s.id, s.name, s) for s in stations]
            coverage = {
                sid: [
                    (_aware(w.start_datetime), _aware(w.end_datetime))
                    for w in windows if window_covers_station(w, sid)
                ]
                for sid, _, _ in stations
            }

            for station_id, station_name, station in stations:
                try:
                    own = coverage[station_id]
                    max_end = max((end for _, end in own), default=None)
                    near_term_count = sum(1 for start, _ in own if start <= next_week)

                    # Extend if coverage < 365 days OR no near-term windows exist
                    if not max_end or (max_end - now).days < 365 or near_term_count == 0:
                        station = await db.get(Station, station_id)
                        count = await auto_generate_holidays_for_station(db, station)
                        if count > 0:
                            await db.commit()
                            logger.info("Extended holidays for station %s: %d new windows", station_name, count)
                except Exception as e:
                    logger.warning("Holiday extension failed for station %s: %s", station_name, e)
        except Exception as e:
            logger.error("Holiday extension check failed: %s", e, exc_info=True)

    async def _maybe_generate_weather_readouts(self, db: AsyncSession):
        """Generate weather readouts for stations that have it enabled, once per day."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        try:
            stmt = select(Station).where(Station.is_active == True)
            result = await db.execute(stmt)
            stations = result.scalars().all()

            for station in stations:
                config = (station.automation_config or {}).get("weather_readout", {})
                if not config.get("enabled"):
                    continue

                station_key = str(station.id)
                tz_name = station.timezone or "America/New_York"
                try:
                    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
                except Exception:
                    local_now = datetime.now(timezone.utc)

                today_str = local_now.strftime("%Y-%m-%d")

                # Skip if already checked today
                if self._last_readout_check.get(station_key) == today_str:
                    continue

                # Skip if local time < generate_time
                gen_time_str = config.get("generate_time", "06:00")
                try:
                    parts = gen_time_str.split(":")
                    gen_hour, gen_min = int(parts[0]), int(parts[1])
                except (ValueError, IndexError):
                    gen_hour, gen_min = 6, 0

                if local_now.hour < gen_hour or (local_now.hour == gen_hour and local_now.minute < gen_min):
                    continue

                # A failed attempt waits out its back-off before the next one (same day only)
                retry = self._readout_retry.get(station_key)
                if retry and retry[0] == today_str and time.monotonic() < retry[2]:
                    continue

                made = False
                try:
                    from app.models.weather_readout import WeatherReadout
                    from app.services.weather_readout_service import generate_readout_for_station
                    readout = await generate_readout_for_station(db, station, local_now.date())
                    if readout:
                        await db.commit()
                        logger.info("Auto-generated weather readout for station %s", station.name)
                        made = True
                    else:
                        # None is either "already there" (done) or "could not be made" (retry)
                        made = (await db.execute(select(WeatherReadout.id).where(
                            WeatherReadout.station_id == station.id,
                            WeatherReadout.readout_date == local_now.date(),
                        ).limit(1))).scalar_one_or_none() is not None
                except Exception as e:
                    logger.warning("Weather readout generation failed for station %s: %s", station.name, e)

                if made:
                    self._last_readout_check[station_key] = today_str
                    self._readout_retry.pop(station_key, None)
                else:
                    # Try again later today: the next pass first, then 1, 2, 4 ... up to 30 minutes
                    failures = retry[1] + 1 if retry and retry[0] == today_str else 1
                    wait = 0 if failures == 1 else min(READOUT_RETRY_MAX_SECONDS, 60 * 2 ** (failures - 2))
                    self._readout_retry[station_key] = (today_str, failures, time.monotonic() + wait)

        except Exception as e:
            logger.error("Weather readout generation check failed: %s", e, exc_info=True)

    async def _maybe_queue_weather_readouts(self, db: AsyncSession):
        """Auto-queue recorded weather readouts when their queue_time arrives."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        try:
            from app.models.weather_readout import WeatherReadout
            from app.models.queue_entry import QueueEntry

            stmt = select(WeatherReadout).where(
                WeatherReadout.status == "recorded",
                WeatherReadout.asset_id.isnot(None),
                WeatherReadout.queue_time.isnot(None),
            )
            result = await db.execute(stmt)
            readouts = result.scalars().all()

            for readout in readouts:
                station = await db.get(Station, readout.station_id)
                if not station:
                    continue

                config = (station.automation_config or {}).get("weather_readout", {})
                if not config.get("auto_queue"):
                    continue

                tz_name = station.timezone or "America/New_York"
                try:
                    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
                except Exception:
                    local_now = datetime.now(timezone.utc)

                # Only on its own day: tomorrow's readout, recorded tonight, waits for tomorrow
                if readout.readout_date != local_now.date():
                    continue
                local_time = local_now.time()
                if local_time >= readout.queue_time:
                    entry = QueueEntry(
                        station_id=readout.station_id,
                        asset_id=readout.asset_id,
                        position=0,
                        status="pending",
                    )
                    db.add(entry)
                    readout.status = "queued"
                    await db.commit()
                    logger.info("Auto-queued weather readout %s for station %s", readout.id, station.name)

        except Exception as e:
            logger.error("Weather readout auto-queue check failed: %s", e, exc_info=True)

    async def _check_all_stations(self, db: AsyncSession):
        """Advance every active station: one player each (queue, or the playout engine)."""
        # Periodic Liquidsoap health check
        if settings.liquidsoap_enabled:
            try:
                from app.services.liquidsoap_client import is_alive
                if not await is_alive():
                    logger.warning("Liquidsoap not responding — clients will use fallback")
            except Exception:
                logger.warning("Liquidsoap health check failed", exc_info=True)

        stmt = select(Station).where(Station.is_active == True)
        result = await db.execute(stmt)
        station_ids = [s.id for s in result.scalars().all()]

        for station_id in station_ids:
            # Re-read each station: a rollback after another station's error expires
            # every loaded object, and touching an expired one here would fail
            station = await db.get(Station, station_id)
            if station is None:
                continue
            station_name = station.name
            try:
                # HS off: timed items that arrived since the last pass wait for the song too.
                # Under the station's lock, with HS read fresh, so a switch flipped a
                # moment ago is never undone (wiki/station-modes.md)
                from app.api.v1.queue import advance_lock
                from app.services.station_modes import hard_start_on, soften_hard_items
                if not hard_start_on(station):
                    async with advance_lock(station_id):
                        await db.refresh(station, ["automation_config"])
                        if await soften_hard_items(db, station):
                            await db.commit()
                    station = await db.get(Station, station_id)
                # Shortly before a hard timed item, put a song that ends near it in the
                # place of one that would be faded soon after starting (hour_fit.py)
                from app.services.hour_fit import fit_before_hard_item
                try:
                    async with advance_lock(station_id):
                        if await fit_before_hard_item(db, station):
                            await db.commit()
                except Exception:
                    logger.warning("Fitting songs before the hour failed for station %s", station_id, exc_info=True)
                    await _safe_rollback(db)
                # A song drawn for a block that has ended by the time it would start
                # gives way to the block that is on (hour_fit.drop_songs_of_ended_blocks)
                from app.services.hour_fit import drop_songs_of_ended_blocks
                try:
                    station = await db.get(Station, station_id)
                    async with advance_lock(station_id):
                        if await drop_songs_of_ended_blocks(db, station):
                            await db.commit()
                except Exception:
                    logger.warning("Checking songs of ended blocks failed for station %s", station_id, exc_info=True)
                    await _safe_rollback(db)
                # A sponsorship break the song on air carries into no-sponsorship time
                # is skipped, not aired and not cut for (skip_sponsors_past_quiet)
                from app.services.spot_schedule_service import skip_sponsors_past_quiet
                try:
                    station = await db.get(Station, station_id)
                    async with advance_lock(station_id):
                        skipped = await skip_sponsors_past_quiet(db, station)
                        if skipped:
                            await db.commit()
                    if skipped and settings.engine_drives(station_id):
                        from app.services import playout_engine
                        await playout_engine.take_operator_change(db, station_id)
                except Exception:
                    logger.warning("Skipping sponsorships past quiet time failed for station %s", station_id, exc_info=True)
                    await _safe_rollback(db)
                station = await db.get(Station, station_id)
                if settings.engine_drives(station.id):
                    # The playout engine owns the clock for this station: it asks for the
                    # next track and reports real starts. No time-based advance here.
                    from app.services import playout_engine
                    await playout_engine.tick(db, station)
                else:
                    # Run queue-based playback advancement (handles silence entries during blackout too)
                    await self._advance_queue(db, station_id)
                station = await db.get(Station, station_id)  # fresh if the advance rolled back
                from app.api.v1.queue import note_station_crossfade
                note_station_crossfade(station)
                await self._check_station(db, station)
            except Exception as e:
                logger.error(f"Error checking station {station_id}: {e}", exc_info=True)
                await _safe_rollback(db)
                try:
                    from app.services.alert_service import create_alert
                    await create_alert(
                        db,
                        alert_type="system",
                        severity="critical",
                        title=f"Station check failed: {station_name}",
                        message=str(e),
                        station_id=station_id,
                    )
                    # Kept now: the pass ends in a rollback, which dropped the alert, so
                    # every 3 s pass made (and texted) a new one instead of folding (V1-63)
                    await db.commit()
                except Exception:
                    logger.warning("Could not record the alert for station %s", station_id, exc_info=True)
    
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

        from app.services.station_service import window_covers_station

        for window in windows:
            if window_covers_station(window, station.id):
                return True

        return False

    async def _check_live_show_hard_stop(self, db: AsyncSession, station: Station, live_show_id: str, now: datetime):
        """Check if a live show has reached its hard stop time."""
        from app.models.live_show import LiveShow, LiveShowStatus

        try:
            show_uuid = uuid.UUID(str(live_show_id))
        except ValueError:
            show_uuid = None
        show = None
        if show_uuid is not None:
            result = await db.execute(select(LiveShow).where(LiveShow.id == show_uuid))
            show = result.scalar_one_or_none()

        if not show or show.status != LiveShowStatus.LIVE:
            from app.api.v1.live_shows_ws import live_relay
            air = live_relay.airs.get(str(station.id))
            if air is not None and air.show_id == str(live_show_id) and not air.closed:
                if air.finishing and air.finish_reason != "host left" and not air.drained():
                    return  # the end of the show is still playing out of the broadcast delay
                # A show ended in the moment its host went on air: End found the station
                # not yet taken (nothing to give back), the take-over landed after it,
                # and the host's audio opened a delay for a show that was over. That
                # delay never closed, so this returned for ever and the stream stayed
                # quiet until the next show (KBR Jerusalem, 23:35Z 2026-11-18, 39 s).
                await air.stop()
                live_relay.on_air.pop(str(station.id), None)
            # Stale reference: hand the station back (the queue or engine carries on)
            from app.services.live_show_service import release_station
            if not await release_station(db, station.id, live_show_id, now):
                config = dict(station.automation_config or {})
                config.pop("live_show_id", None)
                station.automation_config = config
            # Each pass ends with a rollback (it closes the read transaction), so a
            # change made here must be committed here or it is lost
            await db.commit()
            logger.info("Cleared stale live_show_id from station %s", station.id)
            return

        end = show.scheduled_end
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes
        # Shabbos or Yom Tov has begun (or is seconds away) on this station: the show
        # stops whatever its planned end says. Its end is moved when it starts
        # (live_show_service.start_show); this catches a window added or changed since.
        from app.services.live_show_service import off_air_now
        shabbos = await off_air_now(db, station.id, now)
        if not (end and end <= now) and not shabbos and await self._host_gone(db, station, show, now):
            return
        if (end and end <= now) or shabbos:
            # Hard stop
            from app.services.live_show_service import hard_stop_show
            await hard_stop_show(db, show_uuid)
            # Committed here: the pass's closing rollback used to undo it, so the show
            # stayed live and was "hard stopped" again every few seconds (2026-11-16)
            await db.commit()
            logger.warning("Hard stopped live show %s on station %s", live_show_id, station.id)

            # Broadcast WS event
            try:
                from app.api.v1.live_shows_ws import broadcast_show_event
                await broadcast_show_event(str(live_show_id), "show_hard_stopped", {
                    "show_id": str(live_show_id),
                })
            except Exception:
                logger.warning("Live show hard-stop broadcast failed", exc_info=True)

    async def _host_gone(self, db: AsyncSession, station: Station, show, now: datetime) -> bool:
        """The station is the show's, but no host audio arrives (the host's page closed,
        their connection froze, or the show was started and no microphone ever came):
        after `live_host_gone_seconds` (station setting, default 15, 0 = never) the
        station goes back to its queue and an alert is raised. The show stays live: a
        host who comes back is on air again through the delay. Before, listeners got
        silence until the show's hard stop and air watch said nothing, because a live
        show owns the air (Jerusalem, 2026-11-18, 38 s of silence). True = handed back."""
        from app.api.v1.live_shows_ws import live_relay

        sid = str(station.id)
        try:
            limit = float((station.automation_config or {}).get("live_host_gone_seconds", 15))
        except (TypeError, ValueError):
            limit = 15.0
        air = live_relay.airs.get(sid)
        if air is not None and air.show_id != str(show.id):
            air = None
        mono = time.monotonic()
        if air is not None and not air.closed:
            left = air.finishing and air.finish_reason == "host left"
            if air.finishing and not left:
                self._host_gone_since.pop(sid, None)
                return False   # the end of the show is still playing out of the delay
            if not left and air.clock() - air.last_input < 3.0:
                self._host_gone_since.pop(sid, None)
                return False   # the host is sending (a muted microphone sends silence, that counts)
            # Nothing arrives, or the host's page closed: counted from the last frame.
            # It used to start again when the delay had played out, so listeners got
            # the delay and then the whole limit as silence (harness, 2026-11-18)
            self._host_gone_since.setdefault(sid, mono - (air.clock() - air.last_input))
            if left:
                return False   # what the host said is still playing out of the delay
        since = self._host_gone_since.setdefault(sid, mono)
        if limit <= 0 or mono - since < limit:
            return False
        self._host_gone_since.pop(sid, None)
        from app.services.live_show_service import release_station
        if air is not None and not air.closed:
            await air.stop()
        live_relay.on_air.pop(sid, None)
        host = live_relay.hosts.pop(sid, None)
        if host is not None:
            try:
                await host.close(code=4000)   # a frozen page finds out and can start again
            except Exception:
                pass
        await release_station(db, station.id, show.id, now)
        try:
            from app.services.alert_service import create_alert
            await create_alert(
                db, alert_type="live_show", severity="critical",
                title=f"Live show lost its host: {show.title}",
                message=(f"No sound came from the host of '{show.title}' for {limit:.0f} seconds, so the "
                         "station went back to its music. The show is still open: when the host starts "
                         "broadcasting again they are back on air."),
                station_id=station.id,
            )
        except Exception:
            logger.warning("Could not raise the lost-host alert", exc_info=True)
        await db.commit()
        logger.warning("Live show %s on station %s lost its host: station handed back", show.id, station.id)
        await live_relay.broadcast_state(sid)
        try:
            from app.api.v1.live_shows_ws import broadcast_show_event
            await broadcast_show_event(str(show.id), "host_gone", {"show_id": str(show.id)})
        except Exception:
            logger.warning("Lost-host broadcast failed", exc_info=True)
        try:
            from app.services.bug_report_service import send_ntfy
            await send_ntfy(f"No sound from the host of '{show.title}' for {limit:.0f} s. The station went back to music.",
                            title=f"On air: {station.name}")
        except Exception:
            logger.warning("Lost-host push failed", exc_info=True)
        return True

    async def _stop_unheard_live_show(self, db: AsyncSession, station: Station, now: datetime):
        """A live show that never took the station over (started, but the host's
        microphone never came, or the delay never filled) still ends at its hard stop.
        The station's `live_show_id` is set only when listeners first hear the host, so
        the check above never saw such a show and it stayed "live" for ever."""
        from app.models.live_show import LiveShow, LiveShowStatus

        mono = time.monotonic()
        if mono - self._unheard_checked.get(station.id, 0.0) < 10.0:
            return
        self._unheard_checked[station.id] = mono
        rows = (await db.execute(select(LiveShow.id, LiveShow.scheduled_end).where(
            LiveShow.station_id == station.id, LiveShow.status == LiveShowStatus.LIVE,
            LiveShow.scheduled_end.is_not(None),
        ))).all()
        for show_id, end in rows:
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if end > now:
                continue
            from app.services.live_show_service import hard_stop_show
            await hard_stop_show(db, show_id)
            await db.commit()
            logger.warning("Hard stopped live show %s on station %s (it was never heard)", show_id, station.id)
            try:
                from app.api.v1.live_shows_ws import broadcast_show_event
                await broadcast_show_event(str(show_id), "show_hard_stopped", {"show_id": str(show_id)})
            except Exception:
                logger.warning("Live show hard-stop broadcast failed", exc_info=True)

    async def _check_silence_detection(self, db: AsyncSession, station: Station, has_playing_asset: bool, is_blacked_out: bool):
        """
        Detect dead air (nothing playing in the queue) and recover.

        If a station has nothing playing for longer than the configured threshold,
        a critical alert is raised once and the queue is refilled and started.
        """
        station_key = str(station.id)
        now = datetime.now(timezone.utc)

        # If there IS a playing asset, clear the silence timer
        if has_playing_asset:
            self._silence_start.pop(station_key, None)
            self._silence_alerted.discard(station_key)
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

        # --- Silence threshold exceeded: dead air detected (alert once per spell) ---
        if station_key not in self._silence_alerted:
            self._silence_alerted.add(station_key)
            logger.critical(
                "Station %s (%s): Dead air detected — no audio for %.0f seconds",
                station.name, station.id, elapsed_seconds,
            )
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
                await db.commit()
            except Exception as e:
                logger.error("Failed to create silence alert for station %s: %s", station.id, e)

        # Recover through the queue, the station's only player: refill it and start
        # the next item, exactly as the Start button does. Never play something
        # beside the queue (that made two players run at once).
        try:
            from app.api.v1.queue import start_from_queue
            entry = await start_from_queue(db, station.id)
            if entry is not None:
                self._silence_start.pop(station_key, None)
                self._silence_alerted.discard(station_key)
                logger.warning("Station %s: dead air ended by starting the queue", station.id)
        except Exception as e:
            logger.error("Station %s: restarting the queue failed: %s", station.id, e, exc_info=True)

    def _schedule_precise_advance(self, station_id, duration_seconds: float, crossfade_seconds: float = 3.0):
        """Schedule a precise timer to advance playback at the right moment."""
        station_key = str(station_id)
        # Cancel any existing timer for this station
        old_timer = self._advance_timers.pop(station_key, None)
        if old_timer:
            old_timer.cancel()

        delay = max(0.0, duration_seconds - crossfade_seconds)
        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            delay,
            lambda: loop.create_task(self._precise_advance(station_id, crossfade_seconds)),
        )
        self._advance_timers[station_key] = handle
        logger.debug("Scheduled precise advance for station %s in %.1fs", station_id, delay)

    async def _precise_advance(self, station_id, crossfade_seconds: float = 0.0):
        """Called by the precise timer, `crossfade_seconds` before the track ends.

        The server changes track at that moment, the same moment clients start their
        crossfade. If it waited for the full duration (and then for the next poll),
        clients would start every song a few seconds before the server and drift
        further ahead with each track.
        """
        if not self.is_leader:
            return  # another process took over between scheduling and firing
        if settings.engine_drives(station_id):
            return
        try:
            async with async_session_factory() as db:
                from app.api.v1.queue import _check_advance
                entry = await _check_advance(db, station_id, early_seconds=crossfade_seconds)
                if entry and entry.status == "playing":
                    await self._broadcast_queue_entry(db, station_id, entry)
                await _safe_rollback(db)
        except Exception as e:
            logger.error("Precise advance failed for station %s: %s", station_id, e, exc_info=True)

    async def _push_to_liquidsoap(self, audio_url: str | None, station_id):
        """Push a track to Liquidsoap if enabled and URL is available."""
        if not audio_url or not settings.liquidsoap_enabled:
            return
        try:
            from app.services.liquidsoap_client import push_track
            await push_track(audio_url, str(station_id))
        except Exception as e:
            logger.warning("Liquidsoap push failed: %s", e)

    def _build_audio_url(self, asset) -> str | None:
        """Build public audio URL for an asset."""
        if not asset or not asset.file_path:
            return None
        file_path = asset.file_path
        if file_path.startswith("http://") or file_path.startswith("https://"):
            return file_path
        if settings.supabase_storage_enabled:
            bucket = settings.SUPABASE_STORAGE_BUCKET
            return f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_path}"
        return None

    async def _advance_queue(self, db: AsyncSession, station_id):
        """Advance queue-based playback: check if current track ended and move to next."""
        try:
            from app.api.v1.queue import _check_advance
            entry = await _check_advance(db, station_id)
            if entry and entry.status == "playing" and entry.started_at:
                # Schedule precise timer for this track
                asset = entry.asset
                duration = (asset.duration if asset else None) or 180.0
                # Advance where clients start their crossfade: the queue's one
                # hand-over rule (short and timed items play to their end)
                from app.api.v1.queue import handover_seconds, is_song, predict_next_entry
                if is_song(entry):
                    # A song is not crossfaded into a spoken item: what follows it
                    # decides when it hands over (noted by predict_next_entry)
                    await predict_next_entry(db, station_id, entry)
                cross_seconds = handover_seconds(entry)

                # Calculate remaining time from started_at
                elapsed = (datetime.now(timezone.utc) - _aware(entry.started_at)).total_seconds()
                remaining = duration - elapsed
                # Past the crossfade point the timer has already fired; arming it again
                # (with a delay of 0) would only start a second advance.
                if remaining > cross_seconds:
                    self._schedule_precise_advance(station_id, remaining, cross_seconds)

                # Push current track to Liquidsoap
                await self._push_to_liquidsoap(self._build_audio_url(asset), station_id)

                # Broadcast expanded WS payload
                await self._broadcast_queue_entry(db, station_id, entry)
        except Exception as e:
            logger.error("Queue advance failed for station %s: %s", station_id, e, exc_info=True)
            # The session is shared by every station in this tick: a failed flush
            # left unrolled-back would fail all of them until the next tick
            await _safe_rollback(db)
        try:
            await self._arm_timed_start(db, station_id)
        except Exception as e:
            logger.warning("Timed start timer failed for station %s: %s", station_id, e)

    # How far ahead the 3 s poll arms an exact timer for a timed item
    TIMED_LOOKAHEAD_SECONDS = 10.0
    # How far ahead pages are told again what is next, so they pre-load a timed item
    TIMED_ANNOUNCE_AHEAD_SECONDS = 60.0

    def _cancel_timed_timers(self):
        for _, timer in self._timed_timers.values():
            timer.cancel()
        self._timed_timers.clear()

    async def _arm_timed_start(self, db: AsyncSession, station_id):
        """Arm an exact timer for the next timed item due within a few seconds.

        The poll runs every 3 s and the song-end timer only covers natural ends, so
        a hard item (hard sponsorship, the hourly time) used to start up to 3 s
        late while the control room had already faded the song out at its time:
        dead air before every hard item. A soft item whose wait runs out (hard_by)
        is due then too.
        """
        from app.models.queue_entry import QueueEntry, is_soft_timed
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(seconds=self.TIMED_LOOKAHEAD_SECONDS)
        announce_horizon = now + timedelta(seconds=self.TIMED_ANNOUNCE_AHEAD_SECONDS)
        rows = (await db.execute(
            select(QueueEntry)
            .where(
                QueueEntry.station_id == station_id,
                QueueEntry.status == "pending",
                QueueEntry.preempt_at.isnot(None),
                QueueEntry.preempt_at <= announce_horizon,
            )
            .order_by(QueueEntry.preempt_at, QueueEntry.position)
            .limit(20)
        )).scalars().all()
        due = None
        for entry in rows:
            at = _aware(entry.preempt_at)
            if is_soft_timed(entry):
                if entry.hard_by is None:
                    continue  # waits for the song to end: the song-end timer covers it
                at = max(at, _aware(entry.hard_by))
            if at <= announce_horizon and (due is None or at < due):
                due = at
        key = str(station_id)
        if due is not None and due > now:
            await self._announce_timed(db, station_id, key, due)
        if due is not None and due > horizon:
            return  # announced; the exact timer is armed in a later poll
        armed = self._timed_timers.get(key)
        if due is None or due <= now:
            return  # nothing soon, or already due: this tick's advance handles it
        if armed and abs((armed[0] - due).total_seconds()) < 0.01:
            return
        if armed:
            armed[1].cancel()
        loop = asyncio.get_running_loop()
        # A hair after the due time, so the advance's "due <= now" check holds
        delay = (due - now).total_seconds() + 0.005
        handle = loop.call_later(
            delay, lambda: loop.create_task(self._timed_fire(station_id, due))
        )
        self._timed_timers[key] = (due, handle)

    async def _announce_timed(self, db: AsyncSession, station_id, key: str, due: datetime):
        """Send the now-playing message again once a hard timed item is less than a minute
        away, so pages learn it is next (with its cut_at), pre-load it and start it at its
        time themselves. The message sent when the item on air started can be older than
        the time check's queue entry (a 30 min story): pages then fetched the time check
        when it was announced and started it 1.1-1.8 s late (13:00Z, 14:00Z 2026-11-17)."""
        if self._timed_announced.get(key) == due:
            return
        self._timed_announced[key] = due
        from sqlalchemy.orm import joinedload

        from app.models.queue_entry import QueueEntry
        playing = (await db.execute(
            select(QueueEntry).options(joinedload(QueueEntry.asset)).where(
                QueueEntry.station_id == station_id, QueueEntry.status == "playing",
            ).limit(1)
        )).unique().scalar_one_or_none()
        if playing is not None and playing.started_at is not None:
            await self._broadcast_queue_entry(db, station_id, playing)

    async def _timed_fire(self, station_id, due):
        current = self._timed_timers.get(str(station_id))
        if current and current[0] == due:
            self._timed_timers.pop(str(station_id), None)
        await self._precise_advance(station_id, 0.0)

    async def _broadcast_queue_entry(
        self, db: AsyncSession, station_id, entry, change_fade_seconds: float | None = None
    ):
        """Broadcast an expanded now_playing update for a queue entry, including analysis data + next track."""
        payload = await self.queue_entry_payload(db, station_id, entry, change_fade_seconds, push=True)
        if payload is None:
            return
        try:
            from app.api.v1.websocket import broadcast_now_playing_update
            await broadcast_now_playing_update(str(station_id), payload)
        except Exception as e:
            logger.error("Failed to broadcast expanded WS update: %s", e)

    async def queue_entry_payload(
        self, db: AsyncSession, station_id, entry, change_fade_seconds: float | None = None,
        push: bool = False,
    ) -> dict | None:
        """The now_playing message for a queue entry: analysis data + next track.
        Also what a listener gets on connecting, so both always agree.
        `push` also pre-queues the next track in Liquidsoap (broadcasts only)."""

        asset = entry.asset
        if not asset:
            return None

        # Get audio analysis data (defaults if missing)
        analysis = {}
        if asset.metadata_extra:
            analysis = stored_analysis(asset.metadata_extra)

        cue_in = analysis.get("cue_in_seconds", 0)
        cue_out = analysis.get("cue_out_seconds", asset.duration or 180.0)
        # Where pages hand over: the same rule the queue advances by, so a short or
        # timed item is not crossfaded 3 s before its end on the page
        from app.api.v1.queue import end_fade_seconds, handover_seconds, overlap_seconds
        replay_gain_db = analysis.get("replay_gain_db", 0)

        # What follows this item: the queue's own pick at the hand-over, so a soft
        # sponsorship due by then is pre-loaded instead of the next song. Asked first:
        # a song followed by a spoken item hands over later (no crossfade into speech).
        from app.api.v1.queue import predict_next_entry
        next_entry = await predict_next_entry(db, station_id, entry)
        cross_start = (asset.duration or 180.0) - handover_seconds(entry)

        # Items without a usable analysis get one now, before their hand-over
        from app.services import upcoming_analysis
        upcoming_analysis.request(asset)
        if next_entry is not None:
            upcoming_analysis.request(next_entry.asset)

        next_asset_data = None
        if next_entry and next_entry.asset:
            na = next_entry.asset
            na_analysis = {}
            if na.metadata_extra:
                na_analysis = stored_analysis(na.metadata_extra)

            # Build audio URL for next asset
            from app.config import settings as app_settings
            na_file_path = na.file_path
            na_audio_url = None
            if na_file_path.startswith("http://") or na_file_path.startswith("https://"):
                na_audio_url = na_file_path
            elif app_settings.supabase_storage_enabled:
                bucket = app_settings.SUPABASE_STORAGE_BUCKET
                na_audio_url = f"{app_settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{na_file_path}"

            next_asset_data = {
                "id": str(na.id),
                "title": na.title,
                "artist": na.artist,
                "audio_url": na_audio_url,
                # A paused shiur coming back starts where it stopped
                "cue_in": max(na_analysis.get("cue_in_seconds", 0) or 0, resume_offset(next_entry)),
                "replay_gain_db": na_analysis.get("replay_gain_db", 0),
                # When it cuts in (a hard timed item's time, or a soft one's limit), so
                # pages start it at that moment themselves instead of a second later
                # when this message reaches them. None = it follows the item on air.
                "cut_at": _cut_time(next_entry, entry),
                "overlap_seconds": overlap_seconds(next_entry),
            }

            # Pre-queue next track in Liquidsoap for gapless transitions
            if push:
                await self._push_to_liquidsoap(na_audio_url, station_id)

        # Build audio URL for current asset
        from app.config import settings as app_settings
        file_path = asset.file_path
        audio_url = None
        if file_path.startswith("http://") or file_path.startswith("https://"):
            audio_url = file_path
        elif app_settings.supabase_storage_enabled:
            bucket = app_settings.SUPABASE_STORAGE_BUCKET
            audio_url = f"{app_settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_path}"

        return {
            "station_id": str(station_id),
            "asset_id": str(asset.id),
            "started_at": entry.started_at.isoformat() if entry.started_at else None,
            "ends_at": (entry.started_at + timedelta(seconds=(asset.duration or 180.0))).isoformat() if entry.started_at else None,
            # When it really went on air. A paused shiur coming back counts as started
            # `offset` s ago (started_at), which is before the time check that paused
            # it: announcements are ordered by this, not by started_at
            "on_air_at": (
                aired_from(entry).isoformat() if entry.started_at else None
            ),
            "stream_url": settings.public_stream_url(station_id),
            "asset": {
                "title": asset.title,
                "artist": asset.artist,
                "album": asset.album,
                "album_art_path": asset.album_art_path,
                "audio_url": audio_url,
                "cue_in": cue_in,
                "intro_seconds": asset.intro_seconds,
                "cue_out": cue_out,
                "cross_start": cross_start,
                # How long it overlaps the next item at its end: the station's song
                # crossfade, 0 for anything spoken (pages never crossfade those)
                "overlap_seconds": overlap_seconds(entry),
                # A song before a spoken item: it fades out over this long to its
                # end and the spoken item starts after it (0 = not that case)
                "end_fade_seconds": end_fade_seconds(entry),
                "replay_gain_db": replay_gain_db,
            },
            "next_asset": next_asset_data,
            # Set only on the message that announces an operator's Next: how long the
            # outgoing item fades (hard / fade / soft). Absent = the player's default.
            **(
                {"change_fade_seconds": change_fade_seconds}
                if change_fade_seconds is not None
                else {}
            ),
        }

    async def _check_station(self, db: AsyncSession, station: Station):
        """Live-show hard stops, blackout silence and dead air for one station. Plays nothing itself."""
        now = datetime.now(timezone.utc)

        # Check if station is in live show mode — skip normal scheduler
        live_show_id = station.automation_config.get("live_show_id") if station.automation_config else None
        if live_show_id:
            await self._check_live_show_hard_stop(db, station, live_show_id, now)
            # The time check still airs on time, over the show's broadcast delay
            try:
                from app.services import live_inserts
                await live_inserts.prepare(db, station, now)
            except Exception:
                logger.warning("Timed items for the live show on station %s failed", station.id, exc_info=True)
                await _safe_rollback(db)
            return  # Skip normal scheduler logic
        await self._stop_unheard_live_show(db, station, now)

        # Check blackout windows — fill silence for active AND upcoming blackouts
        from app.api.v1.queue import fill_blackout_queue
        is_blacked_out = await self._is_station_blacked_out(db, station, now)
        try:
            await fill_blackout_queue(db, station.id)
        except Exception as e:
            logger.warning("fill_blackout_queue failed for station %s: %s", station.id, e)
        # The last song before Shabbos, ending at candle lighting (shabbos_ending)
        try:
            from app.services import shabbos_ending
            await shabbos_ending.reconcile(db, station, now)
        except Exception:
            logger.warning("Shabbos ending failed for station %s", station.id, exc_info=True)
            await _safe_rollback(db)
        if is_blacked_out:
            # Just before it ends, the songs after it are drawn again from the real end
            # time, so the block starting then (Motzei Shabbos) starts with its first step
            try:
                from app.services.schedule_refresh_service import redraw_before_blackout_end
                await redraw_before_blackout_end(db, station, now)
            except Exception:
                logger.warning("Redraw before the blackout ends failed for station %s", station.id, exc_info=True)
                await _safe_rollback(db)
            # Let normal queue advancement handle the silence entries
            return

        # The queue is the station's only player (the playout engine plays it when it
        # drives the station). This check used to run a second, block-based player
        # beside it, writing now_playing and play_logs and telling listeners to
        # change song; it no longer plays anything, it only watches for dead air.
        # A now_playing row left by the old player would still be served by the
        # now-playing poll's fallback; remove it (nothing writes these any more).
        from app.models.now_playing import NowPlaying
        leftover = await db.execute(
            select(NowPlaying.id).where(NowPlaying.station_id == station.id).limit(1)
        )
        if leftover.first() is not None:
            await db.execute(delete(NowPlaying).where(NowPlaying.station_id == station.id))
            await db.commit()
        if settings.engine_drives(station.id):
            return  # the engine has its own emergency fallback
        from app.models.queue_entry import QueueEntry
        playing = await db.execute(
            select(QueueEntry.id).where(
                QueueEntry.station_id == station.id, QueueEntry.status == "playing"
            ).limit(1)
        )
        await self._check_silence_detection(
            db, station, has_playing_asset=playing.first() is not None, is_blacked_out=False
        )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cut_time(entry, on_air=None) -> str | None:
    """When a queued timed entry cuts into the item on air, or None if it waits for it.

    Every item of a program block shares one preempt_at, so the item after the time
    check still carries hh:00 once the time check is on air. A cut time at or before
    the start of the item on air is that group's own: the entry follows it (Main,
    15:00Z 2026-11-17: pages faded the time check and joined the Daf Yomi
    announcement late)."""
    from app.models.queue_entry import is_soft_timed
    if entry is None or entry.preempt_at is None:
        return None
    if is_soft_timed(entry):
        if entry.hard_by is None:
            return None
        cut = max(_aware(entry.preempt_at), _aware(entry.hard_by))
    else:
        cut = _aware(entry.preempt_at)
    started = getattr(on_air, "started_at", None)
    if started is not None and cut <= _aware(started) + timedelta(seconds=1):
        return None
    return cut.isoformat()


# Global scheduler instance
_scheduler_instance: Optional[SchedulerEngine] = None


def note_host_left(station_id, silent_for: float) -> None:
    """The host of a live show stopped sending `silent_for` seconds ago: the wait of
    `live_host_gone_seconds` counts from then, whenever the scheduler next looks.
    (KBR Jerusalem 2026-11-18: with a 15 s delay and a 15 s limit the count began only
    when the delay had played out, 17.5 s of silence after the host's last words.)"""
    if _scheduler_instance is not None:
        _scheduler_instance._host_gone_since.setdefault(str(station_id), time.monotonic() - max(0.0, silent_for))


def forget_host_left(station_id) -> None:
    """The host is back: a later leaving is counted afresh."""
    if _scheduler_instance is not None:
        _scheduler_instance._host_gone_since.pop(str(station_id), None)


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
