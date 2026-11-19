"""Sefiras HaOmer on the hour.

Shmuel, 2026-11-19: "we need to announce the Omer on the hour and it switches to the
next Omer day after Shekia"; then, counted as in the siddur: "Hayom yom echad ba'omer
... We're not counting days and nights, it's days only".

How it works (wiki/omer-count.md):
- The count runs 49 days, from the night of 16 Nisan (the second Seder night) to the
  day before Shavuos (5 Sivan). Day N starts at shekia of the evening before.
- Each station counts by its own place and clock: shekia at the station's coordinates
  (Lakewood or Jerusalem when it has none), the station's own time zone. A station
  setting can move the switch later than shekia (minutes after shekia, e.g. tzeis).
- We count days only, as the siddur says it ("Hayom yom echad ba'omer"; Shmuel
  2026-11-19): "Today is the eighth day, which is one week and one day of the Omer",
  from the switch at shekia until the next shekia, night and day alike.
- One recorded clip per day (49, his father's voice from the voice PC), category
  `omer_count`, `metadata_extra.omer_day`. Without the clip for an hour nothing is
  queued: never a stand-in voice.
- The queue fill puts the clip in each hh:00 group right after the time check (before
  the weather), timed like the time check. Nothing inside Shabbos / Yom Tov silence,
  nothing outside the 49 days; station setting `automation_config.omer_count`.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pyluach.dates import HebrewDate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset

logger = logging.getLogger(__name__)

CATEGORY = "omer_count"
DAYS = 49
SETTING_KEY = "omer_count"
MAX_SWITCH_MINUTES = 90

# Where a station without coordinates counts from, by its time zone
LAKEWOOD = (40.0821, -74.2097)
JERUSALEM = (31.7683, 35.2137)
PLACE_BY_ZONE = {
    "America/New_York": LAKEWOOD,
    "Asia/Jerusalem": JERUSALEM,
    "Asia/Tel_Aviv": JERUSALEM,
    "Israel": JERUSALEM,
}


@dataclass(frozen=True)
class OmerSetting:
    enabled: bool = True
    # 0 = at shekia (Shmuel's rule); a later switch (tzeis) is a number of minutes
    switch_minutes: int = 0

    def as_dict(self) -> dict:
        return {"enabled": self.enabled, "switch_minutes": self.switch_minutes}


def omer_setting(automation_config: dict | None) -> OmerSetting:
    """automation_config.omer_count = {enabled, switch_minutes}; missing = on, at shekia."""
    raw = (automation_config or {}).get(SETTING_KEY)
    if not isinstance(raw, dict):
        return OmerSetting()
    try:
        minutes = int(raw.get("switch_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    return OmerSetting(
        enabled=raw.get("enabled") is not False,
        switch_minutes=max(0, min(MAX_SWITCH_MINUTES, minutes)),
    )


def station_place(latitude: float | None, longitude: float | None, zone_name: str) -> tuple[float, float] | None:
    if latitude is not None and longitude is not None and (latitude or longitude):
        return float(latitude), float(longitude)
    return PLACE_BY_ZONE.get(zone_name)


def _sun(place: tuple[float, float], zone: ZoneInfo, day: date) -> tuple[datetime, datetime]:
    """(sunrise, sunset) on a local date, aware in the station's zone (sunrise is only shown)."""
    from app.services.sun_service import get_sun_times

    times = get_sun_times(place[0], place[1], zone.key, day)
    return times["sunrise"], times["sunset"]


def omer_start(hebrew_year: int) -> date:
    """The civil date of 16 Nisan (Omer day 1) in a Hebrew year."""
    return HebrewDate(hebrew_year, 1, 16).to_pydate()


def omer_day_on(day: date) -> int | None:
    """The Omer day (1-49) whose daytime falls on this civil date, or None."""
    first = omer_start(HebrewDate.from_pydate(day).year)
    n = (day - first).days + 1
    return n if 1 <= n <= DAYS else None


def omer_at(at: datetime, zone: ZoneInfo, place: tuple[float, float], switch_minutes: int = 0) -> int | None:
    """The Omer day a station says at this moment, or None outside the count. The day
    begins at shekia (plus the station's switch minutes) of the evening before."""
    local = at.astimezone(zone)
    today = local.date()
    _, sunset = _sun(place, zone, today)
    if local >= sunset + timedelta(minutes=switch_minutes):
        return omer_day_on(today + timedelta(days=1))
    return omer_day_on(today)


# ── wording ───────────────────────────────────────────────────────────────────

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty"}
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
        "nine": "ninth", "twelve": "twelfth", "twenty": "twentieth", "thirty": "thirtieth",
        "forty": "fortieth"}


def cardinal(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    word = _TENS[tens * 10]
    return f"{word}-{_ONES[ones]}" if ones else word


def ordinal(n: int) -> str:
    words = cardinal(n).split("-")
    last = words[-1]
    words[-1] = _ORD.get(last, last + "th")
    return "-".join(words)


def weeks_and_days(n: int) -> str:
    weeks, days = divmod(n, 7)
    parts = []
    if weeks:
        parts.append(f"{cardinal(weeks)} week" + ("s" if weeks > 1 else ""))
    if days:
        parts.append(f"{cardinal(days)} day" + ("s" if days > 1 else ""))
    return " and ".join(parts)


def wording(n: int) -> str:
    """What the clip for day n says, as the siddur counts (days only, "Hayom yom echad
    ba'omer"; Shmuel 2026-11-19): "Today is the eighth day, which is one week and one
    day of the Omer"."""
    if not 1 <= n <= DAYS:
        raise ValueError("day must be 1-49")
    text = f"Today is the {ordinal(n)} day"
    # Nothing added on Lag BaOmer (Shmuel 2026-11-19: "Don't say it's Lag BaOmer in the counting")
    text += f", which is {weeks_and_days(n)} of the Omer." if n >= 7 else " of the Omer."
    return text


def all_scripts() -> list[dict]:
    return [{"day": n, "file": f"omer-{n:02d}.mp3", "text": wording(n)} for n in range(1, DAYS + 1)]


# ── clips ─────────────────────────────────────────────────────────────────────

def clip_key(asset: Asset) -> int | None:
    """The Omer day a clip says (metadata_extra.omer_day)."""
    meta = asset.metadata_extra if isinstance(asset.metadata_extra, dict) else {}
    try:
        n = int(meta.get("omer_day"))
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= DAYS else None


async def load_clips(db: AsyncSession) -> dict[int, Asset]:
    """Every uploaded Omer clip by day."""
    rows = (await db.execute(select(Asset).where(Asset.category == CATEGORY))).scalars().all()
    out: dict[int, Asset] = {}
    for a in rows:
        key = clip_key(a)
        if key is not None and (a.file_path or "").strip():
            out.setdefault(key, a)
    return out


async def store_uploaded_clip(
    db: AsyncSession, n: int, audio_bytes: bytes, *, source: str = "shprach", script: str | None = None
) -> tuple[Asset, bool]:
    """Store the recording for one Omer day. An existing clip is updated in place, so
    entries already queued play the new recording."""
    from app.services.weather_spot_service import STATION_DISPLAY_NAME, _store_upload, _upload_meta

    if not 1 <= n <= DAYS:
        raise ValueError("day must be 1-49")
    url, duration = await _store_upload(audio_bytes, f"omer/{n:02d}_{source}")
    asset = (await load_clips(db)).get(n)
    replaced = asset is not None
    if asset is None:
        asset = Asset(
            id=uuid.uuid4(),
            title=f"Omer - day {n}",
            artist=STATION_DISPLAY_NAME,
            file_path=url,
            asset_type="jingle",
            category=CATEGORY,
        )
        db.add(asset)
    asset.file_path = url
    asset.duration = duration
    asset.metadata_extra = _upload_meta(source, script or wording(n), omer_day=f"{n:02d}")
    await db.flush()
    logger.info("Stored Omer clip for day %d (replaced=%s)", n, replaced)
    return asset, replaced


def next_count(today: date) -> tuple[date, date]:
    """First and last civil day of the Omer that is running or comes next."""
    year = HebrewDate.from_pydate(today).year
    first = omer_start(year)
    if first + timedelta(days=DAYS - 1) < today:
        first = omer_start(year + 1)
    return first, first + timedelta(days=DAYS - 1)


def as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
