"""On-air self-check: watches what every station really does and raises the alarm.

Started 2026-11-16 after Main's 9 PM time check aired at 9:09 (a shiur in the engine's
timed lane held it back) and nobody but Shmuel noticed. Every few seconds, for every
active station, it checks:

  late_timed     a hard timed item (time check, hard block, hard sponsorship) did not
                 start within LATE_SECONDS of its time, or has not started at all
  hour_missing   a station that has been airing hourly time checks had none start
                 around hh:00
  dead_air       nothing on air for DEAD_AIR_SECONDS (outside blackouts and live shows)
  overrun        the item shown on air ran OVERRUN_SECONDS past its length (the site
                 and what is heard have parted ways)
  engine_drift   engine station: what the engine says it plays is not what the site
                 shows, for DRIFT_SECONDS
  timed_skipped  a time check, weather or sponsorship was marked skipped although
                 nothing else aired in its place

Each finding becomes an alert on the Alerts page (repeats fold into one) and, for the
serious ones, an ntfy push. GET /air-watch shows the recent findings. Only the process
that holds the scheduler lease runs it. See wiki/air-watch.md.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry, hard_timed
from app.models.station import Station

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 5.0
LATE_SECONDS = 8.0            # a hard item this late is a fault (they normally start within 1 s)
HOUR_WINDOW_SECONDS = 20.0    # an hourly time check must start within this of hh:00
HOUR_CHECK_AFTER_SECONDS = 40.0
DEAD_AIR_SECONDS = 20.0
OVERRUN_SECONDS = 30.0
DRIFT_SECONDS = 20.0
LOOKBACK = timedelta(minutes=10)
TIME_CATEGORY = "time_announcement"
WEATHER_CATEGORY = "weather_spot"
PUSH_REPEAT_SECONDS = 15 * 60
RECENT_KEEP = 200
SERIOUS = {"late_timed", "hour_missing", "dead_air", "engine_drift"}


@dataclass
class Finding:
    kind: str
    station_id: str
    station_name: str
    title: str
    detail: str
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    key: str = ""  # one alert per key (an entry, an hour, a spell)


@dataclass
class _StationState:
    dead_since: float | None = None
    drift_since: float | None = None
    drift_entry: str | None = None
    hours_checked: set = field(default_factory=set)


class AirWatch:
    def __init__(self) -> None:
        self.reported: set[str] = set()
        self.recent: deque[dict] = deque(maxlen=RECENT_KEEP)
        self.states: dict[str, _StationState] = {}
        self.last_push: dict[str, float] = {}
        self.last_run: str | None = None
        self.runs = 0

    def state(self, station_id) -> _StationState:
        return self.states.setdefault(str(station_id), _StationState())

    # ── the checks ───────────────────────────────────────────────────────────

    async def check_station(self, db: AsyncSession, station: Station, now: datetime,
                            mono: float | None = None) -> list[Finding]:
        mono = time.monotonic() if mono is None else mono
        sid = str(station.id)
        found: list[Finding] = []
        config = station.automation_config or {}
        if config.get("live_show_id"):
            return found  # a live show owns the air
        from app.api.v1.queue import _is_blacked_out

        blacked_out = await _is_blacked_out(db, station.id)

        def add(kind, title, detail, key):
            found.append(Finding(kind=kind, station_id=sid, station_name=station.name,
                                 title=title, detail=detail, key=f"{kind}:{sid}:{key}"))

        rows = (await db.execute(
            select(QueueEntry).options(joinedload(QueueEntry.asset))
            .where(
                QueueEntry.station_id == station.id,
                or_(
                    QueueEntry.status == "playing",
                    QueueEntry.started_at >= now - LOOKBACK,
                    and_(QueueEntry.preempt_at.isnot(None), QueueEntry.preempt_at >= now - LOOKBACK,
                         QueueEntry.preempt_at <= now),
                ),
            )
        )).unique().scalars().all()
        playing = [e for e in rows if e.status == "playing"]
        current = max(playing, key=lambda e: _aware(e.started_at) if e.started_at else now) if playing else None

        # late_timed: hard items past their time that started late or not at all
        if not blacked_out:
            hard_ids = set((await db.execute(
                select(QueueEntry.id).where(
                    QueueEntry.station_id == station.id, QueueEntry.preempt_at.isnot(None),
                    QueueEntry.preempt_at >= now - LOOKBACK, QueueEntry.preempt_at <= now,
                    hard_timed(),
                )
            )).scalars().all())
            for e in rows:
                if e.id not in hard_ids or e.preempt_at is None:
                    continue
                due = _aware(e.preempt_at)
                title = _title(e)
                if e.started_at is not None:
                    late = (_aware(e.started_at) - due).total_seconds()
                    if late > LATE_SECONDS:
                        add("late_timed", f"{title} aired {late:.0f} s late",
                            f"Due {_local(due, station)}, started {_local(_aware(e.started_at), station)}.",
                            e.id)
                elif e.status == "pending" and (now - due).total_seconds() > LATE_SECONDS:
                    behind = _title(current) if current else "nothing"
                    add("late_timed", f"{title} has not started",
                        f"Due {_local(due, station)}, now {(now - due).total_seconds():.0f} s late. "
                        f"On air instead: {behind}.", e.id)

        # hour_missing: around each hh:00, did a time check start?
        local_now = now.astimezone(_tz(station))
        hour_start = local_now.replace(minute=0, second=0, microsecond=0)
        since_hour = (local_now - hour_start).total_seconds()
        hour_key = hour_start.isoformat()
        st = self.state(sid)
        # With HS (hard start) off the time check waits for the song on air by design
        # (services/station_modes.py), so a late one is not a fault then.
        from app.services.station_modes import hard_start_on

        if (not blacked_out and hard_start_on(station) and HOUR_CHECK_AFTER_SECONDS <= since_hour < 600
                and hour_key not in st.hours_checked):
            st.hours_checked.add(hour_key)
            st.hours_checked = {h for h in st.hours_checked if h >= (hour_start - timedelta(hours=3)).isoformat()}
            if await _airs_hourly_time(db, station.id, now):
                h_utc = hour_start.astimezone(UTC)
                lo, hi = h_utc - timedelta(seconds=HOUR_WINDOW_SECONDS), h_utc + timedelta(seconds=HOUR_WINDOW_SECONDS)
                started = (await db.execute(
                    select(QueueEntry.started_at).join(Asset, Asset.id == QueueEntry.asset_id).where(
                        QueueEntry.station_id == station.id, Asset.category == TIME_CATEGORY,
                        QueueEntry.started_at >= lo, QueueEntry.started_at <= hi,
                    ).limit(1)
                )).scalar()
                if started is None:
                    later = (await db.execute(
                        select(QueueEntry.started_at).join(Asset, Asset.id == QueueEntry.asset_id).where(
                            QueueEntry.station_id == station.id, Asset.category == TIME_CATEGORY,
                            QueueEntry.started_at > hi,
                        ).order_by(QueueEntry.started_at).limit(1)
                    )).scalar()
                    what = (f"it started at {_local(_aware(later), station)}" if later
                            else f"none has started yet; on air: {_title(current) if current else 'nothing'}")
                    add("hour_missing", f"No time check at {hour_start.strftime('%-I %p')}",
                        f"No hourly time check started within {HOUR_WINDOW_SECONDS:.0f} s of "
                        f"{hour_start.strftime('%-I:%M %p')}; {what}.", hour_key)

        # dead_air and overrun
        if current is None:
            if blacked_out:
                st.dead_since = None
            else:
                st.dead_since = st.dead_since or mono
                if mono - st.dead_since >= DEAD_AIR_SECONDS:
                    add("dead_air", "Nothing on air",
                        f"Nothing has been on air for {mono - st.dead_since:.0f} s.", f"{int(st.dead_since)}")
        else:
            st.dead_since = None
            if current.started_at is not None and current.asset is not None and current.asset.duration:
                over = (now - _aware(current.started_at)).total_seconds() - current.asset.duration
                if over > OVERRUN_SECONDS and (current.asset.asset_type or "") != "silence":
                    add("overrun", f"{_title(current)} shown on air {over:.0f} s past its end",
                        "The site still shows an item that should have ended: what listeners hear and "
                        "what the site shows may differ.", current.id)

        # timed_skipped: a time check, weather or sponsorship skipped with nothing aired for it
        for e in rows:
            if e.status != "skipped" or e.preempt_at is None or e.asset is None:
                continue
            important = e.asset.category in (TIME_CATEGORY, WEATHER_CATEGORY) or e.source in ("ad_slot", "block")
            if not important:
                continue
            # Aired in another form: the same item, or another of its kind for the same moment
            # (a weather spot redrawn with a new recording, a time check replaced by a block's)
            def kind(x):
                return x.asset.category if x.asset is not None and x.asset.category in (
                    TIME_CATEGORY, WEATHER_CATEGORY) else None

            twin = any(o.id != e.id and o.preempt_at == e.preempt_at and o.started_at is not None
                       and (o.asset_id == e.asset_id or (kind(e) is not None and kind(o) == kind(e))
                            or o.source == "block")
                       for o in rows)
            if twin:
                continue
            if e.started_at is not None:
                # It went on air and was cut (Panic, Next): not "never aired"
                add("timed_skipped", f"{_title(e)} was cut short",
                    f"Due {_local(_aware(e.preempt_at), station)}; it started at "
                    f"{_local(_aware(e.started_at), station)} and was stopped before its end.", e.id)
            else:
                add("timed_skipped", f"{_title(e)} was skipped",
                    f"Due {_local(_aware(e.preempt_at), station)}; it never aired.", e.id)

        # engine_drift: engine stations only
        if settings.engine_drives(station.id):
            from app.services import playout_engine

            status = await playout_engine.engine_status(station.id)
            engine_entry = (status or {}).get("entry_id") or None
            shown = str(current.id) if current else None
            if status is None or engine_entry != shown:
                if st.drift_since is None or st.drift_entry != engine_entry:
                    st.drift_since, st.drift_entry = mono, engine_entry
                elif mono - st.drift_since >= DRIFT_SECONDS:
                    detail = ("The stream server does not answer." if status is None else
                              f"The stream server plays {status.get('title') or engine_entry}, "
                              f"the site shows {_title(current) if current else 'nothing'}.")
                    add("engine_drift", "Stream server and site disagree", detail, f"{int(st.drift_since)}")
            else:
                st.drift_since = st.drift_entry = None

        return found

    # ── reporting ────────────────────────────────────────────────────────────

    async def report(self, db: AsyncSession, findings: list[Finding]) -> None:
        from app.models.alert import AlertSeverity, AlertType
        from app.services.alert_service import create_alert

        for f in findings:
            if f.key in self.reported:
                continue
            self.reported.add(f.key)
            self.recent.appendleft(asdict(f))
            logger.warning("Air watch %s on %s: %s. %s", f.kind, f.station_name, f.title, f.detail)
            try:
                await create_alert(
                    db, AlertType.PLAYBACK_GAP if f.kind != "dead_air" else AlertType.SILENCE,
                    AlertSeverity.CRITICAL if f.kind in SERIOUS else AlertSeverity.WARNING,
                    title=f"{f.station_name}: {f.title}", message=f.detail,
                    station_id=uuid.UUID(f.station_id), context={"air_watch": f.kind, "key": f.key},
                )
            except Exception as e:
                logger.warning("Air watch alert failed: %s", e)
            if f.kind in SERIOUS:
                await self._push(f)
        if len(self.reported) > 5000:
            self.reported = set(list(self.reported)[-2000:])

    async def _push(self, f: Finding) -> None:
        if not settings.NTFY_TOPIC:
            return
        throttle = f"{f.kind}:{f.station_id}"
        if time.monotonic() - self.last_push.get(throttle, -1e9) < PUSH_REPEAT_SECONDS:
            return
        self.last_push[throttle] = time.monotonic()
        from app.services.bug_report_service import send_ntfy

        await send_ntfy(f"{f.title}. {f.detail}", title=f"On air: {f.station_name}",
                        click="https://kbrlive.com/admin/alerts", tags="rotating_light", priority="high")

    async def run_once(self, db: AsyncSession, now: datetime | None = None) -> list[Finding]:
        now = now or datetime.now(UTC)
        station_ids = (await db.execute(select(Station.id).where(Station.is_active.is_(True)))).scalars().all()
        found: list[Finding] = []
        for station_id in station_ids:
            try:
                # get() reloads a station expired by a rollback after another station failed
                station = await db.get(Station, station_id, populate_existing=True)
                if station is None:
                    continue
                found += await self.check_station(db, station, now)
            except Exception as e:
                logger.warning("Air watch check failed for %s: %s", station_id, e)
                # One station's database error must not abort the session for the rest
                await db.rollback()
        await self.report(db, found)
        await db.commit()
        self.last_run = now.isoformat()
        self.runs += 1
        return found


_watch = AirWatch()


def get_watch() -> AirWatch:
    return _watch


async def watch_loop() -> None:
    from app.db.engine import async_session_factory
    from app.services.scheduler_engine import get_scheduler

    await asyncio.sleep(30)  # let the stations settle after a start
    while True:
        try:
            if get_scheduler().is_leader:
                async with async_session_factory() as db:
                    await _watch.run_once(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Air watch pass failed: %s", e)
        await asyncio.sleep(CHECK_EVERY_SECONDS)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _airs_hourly_time(db: AsyncSession, station_id, now: datetime) -> bool:
    """True when the station aired a time check in the past three hours (so one is expected)."""
    return (await db.execute(
        select(QueueEntry.id).join(Asset, Asset.id == QueueEntry.asset_id).where(
            QueueEntry.station_id == station_id, Asset.category == TIME_CATEGORY,
            QueueEntry.started_at >= now - timedelta(hours=3),
            QueueEntry.started_at < now - timedelta(minutes=10),
        ).limit(1)
    )).first() is not None


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _tz(station) -> ZoneInfo:
    name = station.timezone or "UTC"
    if name == "UTC":  # stations left on UTC run on New York time (see wiki/hourly-slots.md)
        name = "America/New_York"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/New_York")


def _local(t: datetime, station) -> str:
    return t.astimezone(_tz(station)).strftime("%-I:%M:%S %p")


def _title(entry) -> str:
    if entry is None:
        return "nothing"
    asset = entry.asset
    return (asset.title if asset and asset.title else "an item")
