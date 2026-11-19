"""Holiday music: before and during each Jewish holiday, an item from that holiday's
list now and then takes the place of an ordinary pick of the same kind.

Shmuel, 2026-11-19: "starting two weeks before each holiday it should start sprinkling
in music and shiurim related to that holiday", and the regular programming stays as it
is "with every so often a holiday specific song"; he must see what is scheduled and be
able to change it ("starting it 4 weeks before if he wants").

How it works (wiki/holiday-music.md):
- Each holiday has a list (`HolidayItem`): songs and shiurim, for every station or one.
  A listed item airs only through this mix, never as ordinary filler, unless marked
  `all_year`.
- Each station has settings per holiday (`HolidaySetting`, defaults below): on/off, days
  before the first day to start, "1 holiday item in every N" at the start and on the day
  before (the peak, rising evenly in between), and on the holiday's own days on the air
  (Chanukah, Chol Hamoed, Purim); shiurim included or not; stop at the end of the holiday
  or when it starts.
- Both fills (the playlist fill for Main and Jerusalem, the rule fill for All Day Stories)
  ask `HolidayMix.pick` before each ordinary music, shiur or story pick. A song is replaced
  by a holiday song, a shiur or story by a holiday shiur or story; jingles, spots, the time
  and weather, program blocks and songs added to a block by hand are never touched.
- Dates follow the station's own calendar: Israel (one day Yom Tov, Shushan Purim) for a
  station in Israel's time zone, the diaspora calendar otherwise. Days count in the
  station's own date. Shabbos and Yom Tov silence is untouched: nothing is queued then.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pyluach.dates import HebrewDate
from pyluach.hebrewcal import Year
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, not_holiday_only, not_placeholder  # noqa: F401
from app.models.holiday_music import HolidayItem, HolidaySetting
from app.services.shabbos_service import is_israel_timezone

# key, name, Hebrew month and day of the first day, days (diaspora), days (Israel).
# pyluach months: 1 Nissan ... 7 Tishrei ... 12 Adar (13 Adar II in a leap year).
HOLIDAYS: list[tuple[str, str, int, int, int, int]] = [
    ("rosh_hashana", "Rosh Hashana", 7, 1, 2, 2),
    ("yom_kippur", "Yom Kippur", 7, 10, 1, 1),
    ("sukkos", "Sukkos and Simchas Torah", 7, 15, 9, 8),
    ("chanukah", "Chanukah", 9, 25, 8, 8),
    ("tu_bishvat", "Tu B'Shvat", 11, 15, 1, 1),
    ("purim", "Purim", 12, 14, 1, 2),
    ("pesach", "Pesach", 1, 15, 8, 7),
    ("lag_baomer", "Lag BaOmer", 2, 18, 1, 1),
    ("shavuos", "Shavuos", 3, 6, 2, 1),
]
HOLIDAY_NAMES = {k: n for k, n, *_ in HOLIDAYS}

DEFAULTS = {
    "enabled": True,
    "lead_days": 14,
    "start_every": 15,  # about one an hour of 4-minute songs
    "peak_every": 4,
    "during_every": 4,
    "include_shiurim": True,
    "stop": "end",
}
LIMITS = {"lead_days": (0, 90), "start_every": (1, 200), "peak_every": (1, 200), "during_every": (1, 200)}

# The least time between two plays of the same holiday item on a station. A story
# or shiur at most once a day: with 6 hours, All Day Stories' two Chanukah stories
# each aired four times a day already at the starting rate (2026-11-19).
REST = {"music": timedelta(hours=3), "long": timedelta(hours=24)}

MUSIC_TYPES = {"music"}
LONG_TYPES = {"shiur", "stories", "story", "daf_yomi", "lecture"}


def kind_of(asset_type: str | None) -> str | None:
    """What an item may stand in for: "music" for songs, "long" for shiurim and
    stories. Anything else (jingles, spots, time, weather) is never replaced."""
    t = (asset_type or "music").lower()
    if t in MUSIC_TYPES:
        return "music"
    if t in LONG_TYPES:
        return "long"
    return None


@dataclass(frozen=True)
class Season:
    key: str
    name: str
    first: date  # the holiday's first day (it starts the evening before)
    last: date


def holiday_days(key: str, hebrew_year: int, israel: bool) -> tuple[date, date]:
    """First and last day of a holiday in a Hebrew year."""
    _, _, month, day, diaspora_len, israel_len = next(h for h in HOLIDAYS if h[0] == key)
    if key == "purim" and Year(hebrew_year).leap:
        month = 13  # Purim is in Adar II in a leap year
    first = HebrewDate(hebrew_year, month, day).to_pydate()
    length = israel_len if israel else diaspora_len
    return first, first + timedelta(days=length - 1)


def seasons_between(start: date, end: date, israel: bool) -> list[Season]:
    """Every holiday whose days touch start..end, in date order."""
    out = []
    for year in range(HebrewDate.from_pydate(start).year - 1, HebrewDate.from_pydate(end).year + 2):
        for key, name, *_ in HOLIDAYS:
            first, last = holiday_days(key, year, israel)
            if last >= start and first <= end:
                out.append(Season(key, name, first, last))
    return sorted(out, key=lambda s: s.first)


@dataclass
class Setting:
    enabled: bool = DEFAULTS["enabled"]
    lead_days: int = DEFAULTS["lead_days"]
    start_every: int = DEFAULTS["start_every"]
    peak_every: int = DEFAULTS["peak_every"]
    during_every: int = DEFAULTS["during_every"]
    include_shiurim: bool = DEFAULTS["include_shiurim"]
    stop: str = DEFAULTS["stop"]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in DEFAULTS}


def clean_setting(values: dict, base: Setting | None = None) -> Setting:
    """A setting from page input, numbers kept within their limits."""
    s = Setting(**(base.as_dict() if base else {}))
    for k in DEFAULTS:
        if k not in values or values[k] is None:
            continue
        v = values[k]
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, int(v)))
        elif k == "stop":
            v = "start" if v == "start" else "end"
        else:
            v = bool(v)
        setattr(s, k, v)
    return s


def share_on(setting: Setting, season: Season, day: date) -> float:
    """The part of the ordinary picks (0..1) that a holiday item replaces on `day`.

    From `lead_days` before the first day the rate rises evenly from 1 in
    `start_every` to 1 in `peak_every` on the day before; on the holiday's own days
    it is 1 in `during_every` (or nothing when the setting stops at the start)."""
    if not setting.enabled:
        return 0.0
    if season.first <= day <= season.last:
        return 0.0 if setting.stop == "start" else 1.0 / max(1, setting.during_every)
    days_left = (season.first - day).days
    lead = setting.lead_days
    if days_left < 1 or days_left > lead:
        return 0.0
    start, peak = 1.0 / max(1, setting.start_every), 1.0 / max(1, setting.peak_every)
    if lead <= 1:
        return peak
    return start + (peak - start) * (lead - days_left) / (lead - 1)


def in_season(setting: Setting, season: Season, day: date) -> bool:
    return share_on(setting, season, day) > 0


def every_n(share: float) -> int | None:
    """A share as "1 in N" for the page."""
    return round(1 / share) if share > 0 else None


async def load_settings(db: AsyncSession, station_id) -> dict[str, Setting]:
    rows = (await db.execute(select(HolidaySetting).where(HolidaySetting.station_id == station_id))).scalars().all()
    out = {k: Setting() for k, *_ in HOLIDAYS}
    for r in rows:
        if r.holiday in out:
            out[r.holiday] = Setting(
                enabled=r.enabled, lead_days=r.lead_days, start_every=r.start_every,
                peak_every=r.peak_every, during_every=r.during_every,
                include_shiurim=r.include_shiurim, stop=r.stop or "end",
            )
    return out


async def station_items(db: AsyncSession, station_id) -> dict[str, list]:
    """The holiday items this station may play, by holiday: listed for every station
    or for this one, not placeholders or do-not-play, not "not on this station"."""
    from app.services.station_blocks import blocked_asset_ids

    analysis = Asset.metadata_extra["audio_analysis"]
    rows = (
        await db.execute(
            select(
                HolidayItem.holiday, Asset.id, Asset.title, Asset.artist, Asset.duration,
                Asset.asset_type, Asset.category,
                analysis["cross_start_seconds"].astext.label("cross_start"),
                analysis["version"].astext.label("analysis_version"),
            )
            .join(Asset, Asset.id == HolidayItem.asset_id)
            .where(
                or_(HolidayItem.station_id.is_(None), HolidayItem.station_id == station_id),
                not_placeholder(),
                Asset.id.notin_(blocked_asset_ids(station_id)),
            )
            .order_by(HolidayItem.holiday, Asset.title)
        )
    ).all()
    out: dict[str, list] = {}
    for r in rows:
        if all(str(x.id) != str(r.id) for x in out.get(r.holiday, [])):
            out.setdefault(r.holiday, []).append(r)
    return out


@dataclass
class HolidayMix:
    """Decides, pick by pick, when a holiday item stands in for an ordinary one.

    A running credit adds the day's share for every ordinary pick of a kind the
    holiday list has; when it reaches 1 a holiday item is drawn instead. So 1 in 4
    really is every fourth or so pick, evenly spread, not a coin toss that can bring
    three in a row or none for an hour."""

    settings: dict[str, Setting]
    items: dict[str, list]
    zone: ZoneInfo
    israel: bool
    last_used: dict[str, datetime] = field(default_factory=dict)
    credit: float = field(default_factory=random.random)
    seasons: list[Season] = field(default_factory=list)
    # holiday key -> how many items it put in (for notes and tests)
    used: dict[str, int] = field(default_factory=dict)
    # The last song/shiur/story before the next pick was a holiday item: never two
    # in a row, whatever the credit (simulator heard two around a station ID on
    # Erev Chanukah 2026-11-19)
    prev_holiday: bool = False

    @classmethod
    async def for_station(cls, db: AsyncSession, station, start: datetime, last_used_loader=None):
        """None when nothing can be mixed in (no lists, all switched off, nothing in
        season over the next days). `last_used_loader`: async () -> the fill's own
        last-used dict, shared so holiday and ordinary picks see each other."""
        settings = await load_settings(db, station.id)
        if not any(s.enabled for s in settings.values()):
            return None
        items = await station_items(db, station.id)
        if not items:
            return None
        zone = ZoneInfo(station.timezone or "UTC")
        israel = is_israel_timezone(station.timezone)
        day = as_utc(start).astimezone(zone).date()
        mix = cls(settings=settings, items=items, zone=zone, israel=israel)
        mix.seasons = seasons_between(day - timedelta(days=1), day + timedelta(days=120), israel)
        if not any(mix.active(day + timedelta(days=d)) for d in range(0, 4)):
            return None  # nothing in season over the next days: the fill runs as before
        mix.credit = await mix._carried_credit(db, station.id, day)
        if last_used_loader is not None:
            mix.last_used = await last_used_loader()
        else:
            from app.services.last_played import last_played

            mix.last_used = dict(await last_played(db, station.id))
        return mix

    async def _carried_credit(self, db: AsyncSession, station_id, day: date) -> float:
        """Where the count stands for a new fill: the share times the songs and
        shiurim since the last holiday item (aired in the last 12 hours or waiting
        in the queue). A queue is topped up one or two songs at a time, and a fresh
        random start for each top-up made it a coin toss again: the simulator heard
        four holiday songs in a row on Erev Chanukah (2026-11-19)."""
        from app.models.play_log import PlayLog
        from app.models.queue_entry import QueueEntry

        active = self.active(day)
        if not active:
            return self.credit
        share = active[0][1]
        holiday_ids = {str(r.id) for rows in self.items.values() for r in rows}
        since = as_utc(datetime.now(UTC)) - timedelta(hours=12)
        aired = (
            await db.execute(
                select(PlayLog.asset_id, Asset.asset_type)
                .join(Asset, Asset.id == PlayLog.asset_id)
                .where(PlayLog.station_id == station_id, PlayLog.start_utc >= since)
                .order_by(PlayLog.start_utc.desc())
                .limit(60)
            )
        ).all()
        waiting = (
            await db.execute(
                select(QueueEntry.asset_id, Asset.asset_type)
                .join(Asset, Asset.id == QueueEntry.asset_id)
                .where(
                    QueueEntry.station_id == station_id,
                    QueueEntry.status.in_(["pending", "playing"]),
                    QueueEntry.preempt_at.is_(None),
                )
                .order_by(QueueEntry.position)
            )
        ).all()
        sequence = list(reversed(aired)) + list(waiting)
        count = 0
        seen_holiday = False
        for asset_id, asset_type in sequence:
            if str(asset_id) in holiday_ids:
                count, seen_holiday = 0, True
                self.prev_holiday = True
            elif kind_of(asset_type):
                count += 1
                self.prev_holiday = False
        if not seen_holiday and count < 1 / share:
            return self.credit  # nothing to go by yet: a random start
        return min(count * share, 0.999)

    def active(self, day: date) -> list[tuple[Season, float]]:
        """Holidays in season on `day` with their share: the one under way first,
        then the nearest coming one (Yom Kippur's run-up overlaps Rosh Hashana's)."""
        out = []
        for s in self.seasons:
            if s.key not in self.items:
                continue
            share = share_on(self.settings.get(s.key, Setting()), s, day)
            if share > 0:
                out.append((s, share))
        return sorted(out, key=lambda x: (not (x[0].first <= day <= x[0].last), x[0].first))

    def _candidates(self, key: str, kind: str) -> list:
        setting = self.settings.get(key, Setting())
        if kind == "long" and not setting.include_shiurim:
            return []
        return [r for r in self.items.get(key, []) if kind_of(r.asset_type) == kind]

    def pick(self, asset_type: str | None, at: datetime):
        """Called before an ordinary pick of `asset_type` expected to start at `at`.
        Returns (row, holiday key) to air instead, or None to go on as usual."""
        kind = kind_of(asset_type)
        if kind is None:
            return None
        day = as_utc(at).astimezone(self.zone).date()
        for season, share in self.active(day):
            rows = self._candidates(season.key, kind)
            if not rows:
                continue
            self.credit += share
            if self.credit < 1.0 or self.prev_holiday:
                self.prev_holiday = False
                return None
            # The longest rested first; equal ones at random. An item that played
            # within its rest time is not used again: a short list then airs less
            # often than the rate rather than the same song every half hour.
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            rest = REST[kind]
            rested = [r for r in rows if as_utc(at) - self.last_used.get(str(r.id), epoch) >= rest]
            if not rested:
                self.credit = min(self.credit, 1.0)  # no burst once one is rested again
                self.prev_holiday = False
                return None
            self.credit -= 1.0
            row = min(rested, key=lambda r: (self.last_used.get(str(r.id), epoch), random.random()))
            self.last_used[str(row.id)] = as_utc(at)
            self.used[season.key] = self.used.get(season.key, 0) + 1
            self.prev_holiday = True
            return row, season.key
        self.prev_holiday = False
        return None


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def holidays_for_assets(db: AsyncSession, station, asset_ids, when: datetime | None = None) -> dict[str, str]:
    """For the queue page: asset id -> holiday name, for items on a list of a holiday
    in season on this station now (so the operator sees where the holiday items air)."""
    ids = [uuid.UUID(str(a)) if not isinstance(a, uuid.UUID) else a for a in asset_ids if a]
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(HolidayItem.asset_id, HolidayItem.holiday).where(
                HolidayItem.asset_id.in_(ids),
                or_(HolidayItem.station_id.is_(None), HolidayItem.station_id == station.id),
            )
        )
    ).all()
    if not rows:
        return {}
    settings = await load_settings(db, station.id)
    zone = ZoneInfo(station.timezone or "UTC")
    israel = is_israel_timezone(station.timezone)
    today = as_utc(when or datetime.now(UTC)).astimezone(zone).date()
    seasons = seasons_between(today - timedelta(days=1), today + timedelta(days=100), israel)
    live = {
        s.key for s in seasons
        for d in range(0, 3)
        if in_season(settings.get(s.key, Setting()), s, today + timedelta(days=d))
    }
    out: dict[str, str] = {}
    for asset_id, key in rows:
        if key in live:
            out[str(asset_id)] = HOLIDAY_NAMES.get(key, key)
    return out


async def space_out(db: AsyncSession, station_id) -> int:
    """Swap apart two holiday items that wait next to each other in the queue (songs,
    shiurim and stories counted; jingles and timed items skipped). The fill spaces
    them, but later edits to the queue (sponsorships planned in, top-ups after
    deletions, reordering before a hard item) put two Chanukah songs back to back in
    the simulator (2026-11-19). The second one changes place with the next ordinary
    automatic item of its kind. The item on air, what the engine holds and the next
    two waiting are left alone. Returns swaps made; does not commit."""
    from sqlalchemy.orm import joinedload

    from app.models.queue_entry import QueueEntry
    from app.services.playout_engine import held_entry_ids

    holiday_ids = {str(a) for a in (await db.execute(
        select(HolidayItem.asset_id).where(
            or_(HolidayItem.station_id.is_(None), HolidayItem.station_id == station_id))
    )).scalars().all()}
    if not holiday_ids:
        return 0
    rows = (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )).unique().scalars().all()
    held = held_entry_ids(station_id)
    ordinary = [e for e in rows if e.preempt_at is None and e.asset is not None and kind_of(e.asset.asset_type)]
    frozen = {e.id for e in rows if e.status == "playing" or e.id in held}
    frozen |= {e.id for e in [e for e in rows if e.status == "pending"][:2]}

    def is_holiday(e) -> bool:
        return str(e.asset_id) in holiday_ids

    swaps = 0
    i = 1
    while i < len(ordinary):
        prev, cur = ordinary[i - 1], ordinary[i]
        if is_holiday(prev) and is_holiday(cur) and cur.id not in frozen and cur.source == "auto":
            kind = kind_of(cur.asset.asset_type)
            for j in range(i + 1, len(ordinary)):
                other = ordinary[j]
                if (other.source == "auto" and not is_holiday(other) and other.id not in frozen
                        and kind_of(other.asset.asset_type) == kind):
                    cur.position, other.position = other.position, cur.position
                    ordinary[i], ordinary[j] = other, cur
                    swaps += 1
                    break
        i += 1
    if swaps:
        await db.flush()
    return swaps
