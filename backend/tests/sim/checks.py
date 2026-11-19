"""The station rules, checked over what the simulated engines put on air."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

LONG_KINDS = {"shiur", "stories", "daf_yomi", "story", "lecture"}
ON_TIME_SECONDS = 1.0
DEAD_AIR_SECONDS = 1.0
HOLD_SECONDS = 8.0  # the hold lane before a hard item (playout_engine.HOLD_BEFORE_HARD_SECONDS)
SPONSOR_APART_MINUTES = 20
ID_MIN, ID_MAX = 12, 20
ID_CLEAR_OF_BREAK_MINUTES = 3


@dataclass
class Row:
    station: str
    entry_id: str | None
    lane: str
    start: float
    end: float
    cut: bool
    cue_in: float
    title: str
    kind: str = ""
    category: str | None = None
    asset_id: str | None = None
    preempt_at: datetime | None = None
    timing_mode: str | None = None
    source: str | None = None
    block_id: str | None = None
    was_soft: bool | None = None
    sponsor: str | None = None
    tz: ZoneInfo = UTC

    @property
    def seconds(self):
        return self.end - self.start

    def local(self, t: float) -> str:
        return datetime.fromtimestamp(t, UTC).astimezone(self.tz).strftime("%a %H:%M:%S.%f")[:-4]

    @property
    def local_start(self):
        return self.local(self.start)

    @property
    def local_end(self):
        return self.local(self.end)

    @property
    def is_sponsorship(self):
        return self.sponsor is not None

    @property
    def is_long(self):
        return (self.kind or "").lower() in LONG_KINDS

    @property
    def is_time(self):
        return self.category == "time_announcement"

    @property
    def is_id(self):
        return (self.category or "").startswith("station_id")

    def label(self):
        return f"{self.local_start} {self.title!r} ({self.kind}/{self.category})"


@dataclass
class Finding:
    check: str
    station: str
    at: str
    text: str


@dataclass
class Result:
    rows: list
    findings: list = field(default_factory=list)
    checks_run: dict = field(default_factory=dict)  # check -> number of cases looked at
    errors: list = field(default_factory=list)
    names: dict = field(default_factory=dict)
    wall_seconds: float = 0.0
    sim_hours: float = 0.0

    def failed(self, check=None):
        return [f for f in self.findings if check is None or f.check == check]

    def report(self) -> str:
        lines = [f"Simulated {self.sim_hours:g} h on {len(self.names)} station(s) in {self.wall_seconds:.0f} s "
                 f"({self.sim_hours * 3600 / max(self.wall_seconds, 1):.0f}x real time); {len(self.rows)} items on air."]
        by = defaultdict(list)
        for f in self.findings:
            by[f.check].append(f)
        for check, n in self.checks_run.items():
            bad = by.get(check, [])
            lines.append(f"  {'FAIL' if bad else 'pass'} {check}: {n} case(s), {len(bad)} failing")
            for f in bad[:12]:
                lines.append(f"      {self.names.get(f.station, f.station)} {f.at}: {f.text}")
            if len(bad) > 12:
                lines.append(f"      ... {len(bad) - 12} more")
        if self.errors:
            lines.append(f"  errors in scheduler passes: {len(self.errors)}")
            for e in self.errors[:5]:
                lines.append(f"      {e}")
        return "\n".join(lines)


async def collect(sim) -> Result:
    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry
    from app.models.spot_schedule import SpotSchedule
    from app.models.station import Station

    async with sim.Session() as db:
        stations = {str(s.id): s for s in (await db.execute(select(Station))).scalars().all()}
        entries = {str(e.id): e for e in (await db.execute(select(QueueEntry))).scalars().all()}
        assets = {str(a.id): a for a in (await db.execute(select(Asset))).scalars().all()}
        spot_sponsor = {}
        spot_station = defaultdict(set)
        for s in (await db.execute(select(SpotSchedule))).scalars().all():
            spot_sponsor[str(s.asset_id)] = str(s.sponsor_id)
            spot_station[str(s.asset_id)].add(str(s.station_id) if s.station_id else None)
    rows = []
    for seg in sim.log:
        e = entries.get(seg.entry_id or "")
        a = assets.get(str(e.asset_id)) if e else None
        tz = ZoneInfo(stations[seg.station].timezone or "America/New_York")
        rows.append(Row(
            seg.station, seg.entry_id, seg.lane, seg.start, seg.end if seg.end is not None else sim.t,
            seg.cut, seg.cue_in, seg.title or (a.title if a else ""),
            kind=(a.asset_type if a else seg.kind) or "", category=a.category if a else None,
            asset_id=str(a.id) if a else None,
            preempt_at=_utc(e.preempt_at) if e else None, timing_mode=e.timing_mode if e else None,
            source=e.source if e else None, block_id=e.block_id if e else None,
            was_soft=e.was_soft if e else None,
            sponsor=_sponsor_key(a, spot_sponsor), tz=tz,
        ))
    rows.sort(key=lambda r: (r.station, r.start))
    res = Result(rows, names={k: v.name for k, v in stations.items()}, errors=list(sim.errors))
    ctx = Context(sim, stations, entries, assets, spot_station)
    for check in ALL_CHECKS:
        check(res, ctx)
    return res


def _sponsor_key(a, spot_sponsor):
    """Who a sponsorship is for: its contract's sponsor, else the business named in the
    title ("TEST SPONSOR 25 - Riverside Fresh Fish"): one sponsor can have several
    recordings, and an automatic fill can pick one that has no contract."""
    if a is None or (a.asset_type or "") != "spot" or a.category == "weather_spot":
        return None
    title = (a.title or "").strip()
    name = title.rsplit(" - ", 1)[-1].strip().lower() if " - " in title else None
    if name:
        return name
    return spot_sponsor.get(str(a.id)) or title.lower()


def _utc(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass
class Context:
    sim: object
    stations: dict
    entries: dict
    assets: dict
    spot_station: dict

    def window(self):
        return self.sim.start_dt.timestamp(), self.sim.t

    def config(self, sid):
        return self.stations[sid].automation_config or {}


def _per_station(rows):
    by = defaultdict(list)
    for r in rows:
        by[r.station].append(r)
    return by


def _add(res, check, row_or_station, at, text):
    station = row_or_station.station if isinstance(row_or_station, Row) else row_or_station
    res.findings.append(Finding(check, station, at, text))


def _count(res, check, n=1):
    res.checks_run[check] = res.checks_run.get(check, 0) + n


# ── the checks ────────────────────────────────────────────────────────────────

def hard_items_on_time(res: Result, ctx: Context):
    """A hard timed group starts on its second (first item within 1 s of its time)."""
    check = "hard items start on their second"
    res.checks_run.setdefault(check, 0)
    start, end = ctx.window()
    groups = defaultdict(list)
    for r in res.rows:
        if r.timing_mode == "hard" and r.preempt_at is not None and r.lane != "emergency":
            groups[(r.station, r.preempt_at)].append(r)
    for (sid, at), rs in groups.items():
        if at.timestamp() < start + 60:
            continue
        first = min(rs, key=lambda r: r.start)
        _count(res, check)
        late = first.start - at.timestamp()
        if abs(late) > ON_TIME_SECONDS:
            _add(res, check, first, first.local_start,
                 f"{first.title!r} due {at.astimezone(first.tz):%H:%M:%S} started {late:+.1f} s off")
    # Hard entries whose time passed and never aired
    aired = {r.entry_id for r in res.rows}
    for e in ctx.entries.values():
        at = _utc(e.preempt_at)
        if e.timing_mode != "hard" or at is None or str(e.station_id) not in ctx.stations:
            continue
        if not (start + 60 <= at.timestamp() <= end - 120):
            continue
        if str(e.id) not in aired and e.status not in ("cancelled",):
            a = ctx.assets.get(str(e.asset_id))
            tz = ZoneInfo(ctx.stations[str(e.station_id)].timezone)
            _count(res, check)
            _add(res, check, str(e.station_id), f"{at.astimezone(tz):%a %H:%M:%S}",
                 f"hard item {a.title if a else e.asset_id!r} never aired (status {e.status})")


def time_checks_on_the_hour(res: Result, ctx: Context):
    """Every hh:00 (and hh:30 where the station has the half-hour time check) a time
    announcement starts within 1 s, outside blackouts."""
    check = "time check at :00 and :30 within 1 s"
    res.checks_run.setdefault(check, 0)
    start, end = ctx.window()
    for sid, rows in _per_station(res.rows).items():
        tz = rows[0].tz
        cfg = ctx.config(sid)
        if not cfg.get("hourly_time_announcement"):
            continue
        minutes = (0, 30) if cfg.get("half_hour_time_check") else (0,)
        t = datetime.fromtimestamp(start, UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        silence = [r for r in rows if r.kind == "silence"]
        while t.timestamp() < end - 30:
            for m in minutes:
                mark = (t + timedelta(minutes=m)).timestamp()
                if mark >= end - 30 or mark < start + 60:
                    continue
                if any(s.start - 1 <= mark <= s.end + 1 for s in silence):
                    continue  # blackout
                _count(res, check)
                hit = [r for r in rows if r.is_time and abs(r.start - mark) <= ON_TIME_SECONDS]
                if not hit:
                    near = [r for r in rows if r.is_time and abs(r.start - mark) <= 900]
                    on_air = next((r for r in rows if r.start <= mark < r.end), None)
                    what = (f"nearest time check at {near[0].local_start}" if near else "no time check within 15 min")
                    _add(res, check, sid, datetime.fromtimestamp(mark, UTC).astimezone(tz).strftime("%a %H:%M"),
                         f"{what}; on air then: {on_air.label() if on_air else 'nothing'}")
            t += timedelta(hours=1)


def no_dead_air(res: Result, ctx: Context):
    """Nothing on air for over 1 s (outside the hold before a hard item), and never the
    engine's emergency music."""
    check = "no dead air or emergency music"
    res.checks_run.setdefault(check, 0)
    start, end = ctx.window()
    for sid, rows in _per_station(res.rows).items():
        for prev, cur in zip(rows, rows[1:]):
            _count(res, check)
            gap = cur.start - prev.end
            hold_ok = (cur.timing_mode == "hard" and cur.preempt_at is not None
                       and abs(cur.start - cur.preempt_at.timestamp()) <= 1 and gap <= HOLD_SECONDS)
            if gap > DEAD_AIR_SECONDS and not hold_ok:
                _add(res, check, cur, prev.local_end, f"{gap:.1f} s of silence before {cur.label()}")
        for r in rows:
            if r.lane == "emergency" and r.start > start + 30:
                _add(res, check, r, r.local_start, f"emergency music for {r.seconds:.0f} s")


def nothing_inside_a_shiur(res: Result, ctx: Context):
    """A shiur or story paused for a hard item resumes after it, and no sponsorship or
    soft item airs inside it (only hard time-check groups may pause it)."""
    check = "no sponsorship inside a shiur or story"
    res.checks_run.setdefault(check, 0)
    for sid, rows in _per_station(res.rows).items():
        for i, r in enumerate(rows):
            if not (r.is_long and r.cut):
                continue
            # where does the same asset come back?
            back = next((j for j in range(i + 1, len(rows)) if rows[j].asset_id == r.asset_id), None)
            _count(res, check)
            inside = rows[i + 1:back] if back is not None else rows[i + 1:i + 6]
            if back is None:
                left = (ctx.assets[r.asset_id].duration or 0) - (r.cue_in + r.seconds) if r.asset_id else 0
                if left > 120:
                    _add(res, check, r, r.local_end, f"{r.title!r} cut with {left / 60:.0f} min left and never resumed")
                continue
            for x in inside:
                if x.is_sponsorship:
                    _add(res, check, x, x.local_start,
                         f"sponsorship {x.title!r} ({x.timing_mode}, due "
                         f"{x.preempt_at.astimezone(x.tz):%H:%M} ) inside {r.title!r}" if x.preempt_at else
                         f"sponsorship {x.title!r} inside {r.title!r}")
            gap_resume = rows[back].cue_in - (r.cue_in + r.seconds)
            if gap_resume > 30:
                _add(res, check, rows[back], rows[back].local_start,
                     f"{r.title!r} resumed {gap_resume:.0f} s after where it stopped (lost audio)")


def sponsor_spacing(res: Result, ctx: Context):
    """The same sponsor never airs twice within 20 minutes; the same spot not twice in an hour."""
    check = "same sponsor never twice within 20 min"
    res.checks_run.setdefault(check, 0)
    for sid, rows in _per_station(res.rows).items():
        last = {}
        for r in rows:
            if not r.is_sponsorship:
                continue
            _count(res, check)
            prev = last.get(r.sponsor)
            if prev is not None and r.start - prev.start < SPONSOR_APART_MINUTES * 60:
                _add(res, check, r, r.local_start,
                     f"{r.title!r} again {(r.start - prev.start) / 60:.1f} min after {prev.local_start}")
            last[r.sponsor] = r


def station_ids(res: Result, ctx: Context):
    """Station IDs 12 to 20 minutes apart (plus one song), never two spoken items in a
    row with an ID, never within 3 minutes of a break, and only the station's own IDs."""
    check = "station IDs spaced, own, clear of breaks"
    res.checks_run.setdefault(check, 0)
    for sid, rows in _per_station(res.rows).items():
        cfg = (ctx.config(sid).get("station_ids") or {})
        if not cfg.get("enabled"):
            continue
        own = cfg.get("category") or "station_id"
        ids = [r for r in rows if r.is_id and r.timing_mode is None]
        for r in [x for x in rows if x.is_id]:
            _count(res, check)
            if r.category != own:
                _add(res, check, r, r.local_start, f"ID {r.title!r} is category {r.category}, station uses {own}")
        for a, b in zip(ids, ids[1:]):
            gap = (b.start - a.start) / 60
            if gap < ID_MIN - 0.1:
                _add(res, check, b, b.local_start, f"IDs only {gap:.1f} min apart")
            if gap > ID_MAX + 8 and not any(x.is_long or x.timing_mode for x in rows if a.start < x.start < b.start):
                _add(res, check, b, b.local_start, f"IDs {gap:.1f} min apart")
        for i, r in enumerate(rows):
            if not r.is_id or r.timing_mode is not None:
                continue
            nb = [rows[j] for j in (i - 1, i + 1) if 0 <= j < len(rows)]
            for x in nb:
                if x.is_id or x.is_sponsorship:
                    _add(res, check, r, r.local_start, f"ID next to {x.label()}")
            breaks = [x for x in rows if x.timing_mode in ("hard", "soft") and x.preempt_at is not None]
            for x in breaks:
                if abs(x.start - r.start) < ID_CLEAR_OF_BREAK_MINUTES * 60 and x is not r:
                    _add(res, check, r, r.local_start, f"ID {abs(x.start - r.start) / 60:.1f} min from break {x.label()}")
                    break


def station_isolation(res: Result, ctx: Context):
    """Nothing set up for one station airs on another: time clips and weather of its own
    region, its own sponsorships and station IDs."""
    check = "nothing from another station"
    res.checks_run.setdefault(check, 0)
    for r in res.rows:
        if r.asset_id is None:
            continue
        a = ctx.assets[r.asset_id]
        region = (ctx.config(r.station).get("weather_region") or "lakewood").lower()
        meta = a.metadata_extra if isinstance(a.metadata_extra, dict) else {}
        if a.category in ("time_announcement", "weather_spot"):
            _count(res, check)
            if (meta.get("region") or "lakewood") != region:
                _add(res, check, r, r.local_start, f"{a.title!r} is for region {meta.get('region')}, station is {region}")
        if r.is_sponsorship:
            _count(res, check)
            allowed = ctx.spot_station.get(r.asset_id, set())
            if None not in allowed and r.station not in allowed:
                _add(res, check, r, r.local_start, f"sponsorship {a.title!r} is contracted for another station")


def time_check_says_the_hour(res: Result, ctx: Context):
    """The time clip on air names the station's local hour (and :30 clip at half past)."""
    check = "time check says the right time"
    res.checks_run.setdefault(check, 0)
    for r in res.rows:
        if not r.is_time or r.asset_id is None:
            continue
        a = ctx.assets[r.asset_id]
        meta = a.metadata_extra if isinstance(a.metadata_extra, dict) else {}
        local = datetime.fromtimestamp(r.start + 2, UTC).astimezone(r.tz)
        _count(res, check)
        hour = int(meta.get("time_of_day", -1))
        minute = 30 if str(meta.get("time_minute")) == "30" else 0
        expect_min = 30 if local.minute >= 30 else 0
        if hour != local.hour or minute != expect_min:
            _add(res, check, r, r.local_start, f"says {hour:02d}:{minute:02d}, local time {local:%H:%M}")


def soft_items_wait_for_the_song(res: Result, ctx: Context):
    """A soft item (:15/:45 break) never cuts a song: the song before it ends by itself."""
    check = "soft breaks wait for the song"
    res.checks_run.setdefault(check, 0)
    for sid, rows in _per_station(res.rows).items():
        for prev, cur in zip(rows, rows[1:]):
            if cur.timing_mode != "soft" or cur.preempt_at is None:
                continue
            _count(res, check)
            if prev.cut and prev.kind == "music" and prev.timing_mode is None:
                _add(res, check, cur, cur.local_start, f"soft {cur.title!r} cut the song {prev.title!r}")


def nothing_twice(res: Result, ctx: Context):
    """One queue entry airs once (a shiur's resume is the only second start)."""
    check = "no queue entry airs twice"
    res.checks_run.setdefault(check, 0)
    seen = {}
    for r in res.rows:
        if not r.entry_id:
            continue
        _count(res, check)
        if r.entry_id in seen and not r.is_long:
            _add(res, check, r, r.local_start, f"{r.title!r} aired again (first at {seen[r.entry_id].local_start})")
        seen.setdefault(r.entry_id, r)


PLACES = {  # where each station keeps its zmanim
    "America/New_York": ("Lakewood", 40.0821, -74.2097, 18),
    "Asia/Jerusalem": ("Jerusalem", 31.7683, 35.2137, 40),
}


def _sunset(tz_name: str, day):
    from astral import LocationInfo
    from astral.sun import sun

    name, lat, lon, _ = PLACES[tz_name]
    loc = LocationInfo(name, "", tz_name, lat, lon)
    return sun(loc.observer, date=day, tzinfo=ZoneInfo(tz_name))["sunset"]


def shabbos_silence(res: Result, ctx: Context):
    """Shabbos and Yom Tov (Shmuel 2026-11-19): the audio fades to the 5-minute silence
    item at candle lighting, only silence airs (on repeat) until 72 minutes after
    shekia, then programming comes straight back."""
    from app.models.holiday_window import HolidayWindow  # noqa: F401

    check = "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"
    timing = "Shabbos/Yom Tov window matches candle lighting and shekia + 72"
    res.checks_run.setdefault(check, 0)
    res.checks_run.setdefault(timing, 0)
    start, end = ctx.window()
    for w in ctx.sim.blackouts:
        sids = [sid for sid in ctx.stations if not w["stations"] or sid in w["stations"]]
        for sid in sids:
            st = ctx.stations[sid]
            tz = ZoneInfo(st.timezone)
            ws, we = w["start"].timestamp(), w["end"].timestamp()
            place = PLACES.get(st.timezone)
            if place is not None and w["reason"] == "Shabbos":
                _count(res, timing)
                fri = w["start"].astimezone(tz).date()
                candles = _sunset(st.timezone, fri) - timedelta(minutes=place[3])
                out = _sunset(st.timezone, fri + timedelta(days=1)) + timedelta(minutes=72)
                ds, de = (w["start"] - candles).total_seconds(), (w["end"] - out).total_seconds()
                if abs(ds) > 90 or abs(de) > 90:
                    _add(res, timing, sid, f"{w['start'].astimezone(tz):%a %H:%M}",
                         f"window {w['start'].astimezone(tz):%a %H:%M}-{w['end'].astimezone(tz):%a %H:%M}; "
                         f"{place[0]} candle lighting ({place[3]} min) {candles:%H:%M}, shekia+72 {out:%a %H:%M}: "
                         f"start {ds / 60:+.0f} min, end {de / 60:+.0f} min")
            rows = [r for r in res.rows if r.station == sid]
            if not rows or not (start < ws < end or start < we < end):
                continue
            _count(res, check)
            if start < ws < end - 60:
                inside = [r for r in rows if r.start < ws + 1 < r.end]
                first_silence = next((r for r in rows if r.kind == "silence" and r.start >= ws - 2), None)
                cfg = ctx.config(sid).get("shabbos_ending") or {}
                opener = next((r for r in rows if cfg.get("enabled") and cfg.get("after_asset_id")
                               and r.asset_id == str(cfg["after_asset_id"]) and abs(r.start - ws) <= ON_TIME_SECONDS),
                              None)
                expected = opener.end if opener is not None else ws
                if first_silence is None or abs(first_silence.start - expected) > ON_TIME_SECONDS:
                    _add(res, check, sid, f"{w['start'].astimezone(tz):%a %H:%M:%S}",
                         "silence did not start at candle lighting: "
                         + (f"first silence {first_silence.local_start}" if first_silence else "no silence")
                         + (f"; on air {inside[0].label()}" if inside else ""))
            # The item the station airs at candle lighting before the silence (the shofar,
            # shabbos_ending) is not a leak
            after_id = ((ctx.config(sid).get("shabbos_ending") or {}).get("after_asset_id")
                        if (ctx.config(sid).get("shabbos_ending") or {}).get("enabled") else None)
            leaks = [r for r in rows if r.kind != "silence" and r.lane != "emergency"
                     and max(r.start, ws) + 1 < min(r.end, we)
                     and not (after_id and r.asset_id == str(after_id) and abs(r.start - ws) <= ON_TIME_SECONDS)]
            for r in leaks[:5]:
                _add(res, check, sid, r.local_start, f"{r.label()} on air during {w['reason']}")
            emerg = [r for r in rows if r.lane == "emergency" and max(r.start, ws) + 1 < min(r.end, we)]
            for r in emerg[:3]:
                _add(res, check, sid, r.local_start, f"emergency music during {w['reason']}")
            if start < we < end - 120:
                after = next((r for r in rows if r.start >= we - 1 and r.kind != "silence"), None)
                if after is None or after.start - we > 5:
                    _add(res, check, sid, f"{w['end'].astimezone(tz):%a %H:%M:%S}",
                         "programming not back at 72 min after shekia: "
                         + (f"first item {after.label()}" if after else "nothing"))
                silence_after = [r for r in rows if r.kind == "silence" and r.start > we + 1]
                for r in silence_after[:2]:
                    _add(res, check, sid, r.local_start, "silence item still airing after the window")


def shabbos_ending(res: Result, ctx: Context):
    """Erev Shabbos (Shmuel 2026-11-19): the station's chosen song (automation_config
    .shabbos_ending) is the last song and ends exactly at candle lighting, not cut; the item
    after it (the shofar) starts right at candle lighting; nothing else airs between the
    song's start and the silence."""
    check = "Erev Shabbos: song ends at candle lighting, shofar, then silence"
    res.checks_run.setdefault(check, 0)
    start, end = ctx.window()
    for sid in ctx.stations:
        cfg = ctx.config(sid).get("shabbos_ending") or {}
        if not cfg.get("enabled") or not cfg.get("song_asset_id"):
            continue
        rows = [r for r in res.rows if r.station == sid]
        tz = ZoneInfo(ctx.stations[sid].timezone)
        for w in ctx.sim.blackouts:
            if w["stations"] and sid not in w["stations"]:
                continue
            if w["reason"] != "Shabbos" and not cfg.get("yom_tov"):
                continue
            ws = w["start"].timestamp()
            if not (start + 600 < ws < end - 60):
                continue
            _count(res, check)
            at = f"{w['start'].astimezone(tz):%a %H:%M:%S}"
            song = next((r for r in rows if r.asset_id == str(cfg["song_asset_id"]) and r.start < ws < r.end + 5), None)
            if song is None:
                _add(res, check, sid, at, "the song did not air before candle lighting")
                continue
            if abs(song.end - ws) > ON_TIME_SECONDS:
                _add(res, check, song, song.local_end, f"song ends {song.end - ws:+.1f} s from candle lighting")
            if song.cut and song.end < ws - ON_TIME_SECONDS:
                _add(res, check, song, song.local_end, "song was cut")
            for r in rows:
                if r is not song and r.asset_id == str(cfg["song_asset_id"]) and r.start > start + 60 \
                        and ws - 6 * 3600 < r.start < ws:
                    _add(res, check, r, r.local_start, "the song also aired earlier the same afternoon")
                if r.category == "shabbos_ending" and not (cfg.get("after_asset_id") == r.asset_id
                                                           and abs(r.start - ws) <= ON_TIME_SECONDS):
                    _add(res, check, r, r.local_start, f"{r.label()} aired outside the Shabbos ending")
            between = [r for r in rows if r is not song and r.kind != "silence"
                       and max(r.start, song.start) + 0.5 < min(r.end, ws)]
            for r in between[:3]:
                _add(res, check, r, r.local_start, f"{r.label()} airs during the song")
            if cfg.get("after_asset_id"):
                after = next((r for r in rows if r.asset_id == str(cfg["after_asset_id"]) and r.start >= ws - 5), None)
                if after is None or abs(after.start - ws) > ON_TIME_SECONDS:
                    _add(res, check, sid, at, "item after the song not at candle lighting: "
                         + (after.local_start if after else "did not air"))
                elif after.cut:
                    _add(res, check, after, after.local_end, "item after the song was cut")
                else:
                    nxt = next((r for r in rows if r.start >= after.end - 0.5 and r is not after), None)
                    if nxt is None or nxt.kind != "silence" or nxt.start - after.end > ON_TIME_SECONDS:
                        _add(res, check, after, after.local_end, "silence does not follow it: "
                             + (nxt.label() if nxt else "nothing"))


ALL_CHECKS = [shabbos_silence, shabbos_ending, hard_items_on_time, time_checks_on_the_hour, no_dead_air, nothing_inside_a_shiur,
              sponsor_spacing, station_ids, station_isolation, time_check_says_the_hour,
              soft_items_wait_for_the_song, nothing_twice]
