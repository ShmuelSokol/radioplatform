"""
Shabbos & Yom Tov blackout window generation service.

Generates HolidayWindow dicts for:
- Every Shabbos (candle lighting → Saturday sunset +72 min); candle lighting is
  Friday sunset −18 min, or −40 min for a station in Israel (Jerusalem custom)
- All Yom Tov days with proper Hebrew→Gregorian conversion: the 2-day diaspora
  calendar, or the 1-day Israel calendar for a station whose time zone is in Israel
"""
import logging
from datetime import date, datetime, timedelta

from pyluach.dates import HebrewDate

from app.services.sun_service import get_sun_times

logger = logging.getLogger(__name__)

# Yom Tov dates in the Hebrew calendar (month, day) pairs.
# Each entry: (name, list of (hebrew_month, hebrew_day) for erev through last day).
# Diaspora 2-day Yom Tov (Israel stations use YOM_TOV_DEFINITIONS_ISRAEL below).
YOM_TOV_DEFINITIONS = [
    ("Rosh Hashanah", [(7, 1), (7, 2)]),          # Tishrei 1-2
    ("Yom Kippur", [(7, 10)]),                      # Tishrei 10
    ("Sukkot", [(7, 15), (7, 16)]),                 # Tishrei 15-16
    ("Shemini Atzeret / Simchat Torah", [(7, 22), (7, 23)]),  # Tishrei 22-23
    ("Pesach (First Days)", [(1, 15), (1, 16)]),    # Nissan 15-16
    ("Pesach (Last Days)", [(1, 21), (1, 22)]),     # Nissan 21-22
    ("Shavuot", [(3, 6), (3, 7)]),                  # Sivan 6-7
]

# In Israel every Yom Tov but Rosh Hashanah is one day (no second day of galuyot).
YOM_TOV_DEFINITIONS_ISRAEL = [
    (name, days if name == "Rosh Hashanah" else days[:1])
    for name, days in YOM_TOV_DEFINITIONS
]

ISRAEL_TIMEZONES = {"Asia/Jerusalem", "Asia/Tel_Aviv", "Israel"}


def is_israel_timezone(tz: str | None) -> bool:
    """A station keeps the Israel calendar when its time zone is Israel's."""
    return (tz or "") in ISRAEL_TIMEZONES


# Candle lighting before sunset: 18 minutes (Lakewood), 40 in Jerusalem. Shmuel
# 2026-11-19: silence from candle lighting until 72 minutes after shekia. KBR Jerusalem's
# window started 18 min before sunset, 22 minutes after Jerusalem candle lighting.
CANDLE_LIGHTING_MINUTES = 18
CANDLE_LIGHTING_MINUTES_ISRAEL = 40
AFTER_SHEKIA_MINUTES = 72


def candle_lighting_minutes(tz: str | None) -> int:
    return CANDLE_LIGHTING_MINUTES_ISRAEL if is_israel_timezone(tz) else CANDLE_LIGHTING_MINUTES


def _sunset_for(lat: float, lon: float, tz: str, d: date) -> datetime:
    """Get timezone-aware sunset datetime for a location and date."""
    times = get_sun_times(lat, lon, tz, d)
    return times["sunset"]


def generate_shabbos_windows(
    lat: float,
    lon: float,
    tz: str,
    start_date: date,
    end_date: date,
    station_ids: list[str],
) -> list[dict]:
    """Generate blackout windows for every Shabbos in the date range.

    start = candle lighting: Friday sunset − 18 min (− 40 min in Israel)
    end   = Saturday sunset + 72 min
    """
    windows = []
    # Find the first Friday on or after start_date
    d = start_date
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)

    while d <= end_date:
        try:
            friday_sunset = _sunset_for(lat, lon, tz, d)
            saturday = d + timedelta(days=1)
            saturday_sunset = _sunset_for(lat, lon, tz, saturday)

            window_start = friday_sunset - timedelta(minutes=candle_lighting_minutes(tz))
            window_end = saturday_sunset + timedelta(minutes=AFTER_SHEKIA_MINUTES)

            windows.append({
                "name": f"Shabbos {d.strftime('%b %d, %Y')}",
                "start_datetime": window_start.isoformat(),
                "end_datetime": window_end.isoformat(),
                "is_blackout": True,
                "affected_stations": {"station_ids": station_ids},
                "reason": "Shabbos",
            })
        except Exception as e:
            logger.warning(f"Could not compute Shabbos window for {d}: {e}")

        d += timedelta(days=7)

    return windows


def _hebrew_to_gregorian(hebrew_year: int, hebrew_month: int, hebrew_day: int) -> date:
    """Convert a Hebrew date to Gregorian."""
    hd = HebrewDate(hebrew_year, hebrew_month, hebrew_day)
    return hd.to_pydate()


def _get_hebrew_years_for_range(start_date: date, end_date: date) -> list[int]:
    """Get all Hebrew years that overlap with the Gregorian date range."""
    start_hd = HebrewDate.from_pydate(start_date)
    end_hd = HebrewDate.from_pydate(end_date)
    return list(range(start_hd.year, end_hd.year + 1))


def generate_yom_tov_windows(
    lat: float,
    lon: float,
    tz: str,
    start_date: date,
    end_date: date,
    station_ids: list[str],
) -> list[dict]:
    """Generate blackout windows for all Yom Tov in the date range.

    A time zone in Israel gets the 1-day Israel calendar, anywhere else the
    2-day diaspora one.

    For each Yom Tov block:
      start = candle lighting on erev (day before first day): sunset − 18 min (− 40 in Israel)
      end   = last day sunset + 72 min
    """
    windows = []
    hebrew_years = _get_hebrew_years_for_range(start_date, end_date)
    definitions = YOM_TOV_DEFINITIONS_ISRAEL if is_israel_timezone(tz) else YOM_TOV_DEFINITIONS

    for hebrew_year in hebrew_years:
        for name, day_list in definitions:
            try:
                # Convert all days to Gregorian
                greg_days = []
                for h_month, h_day in day_list:
                    gd = _hebrew_to_gregorian(hebrew_year, h_month, h_day)
                    greg_days.append(gd)

                first_day = min(greg_days)
                last_day = max(greg_days)

                # Skip if entirely outside our range
                if last_day < start_date or first_day > end_date:
                    continue

                # Erev = day before first day
                erev = first_day - timedelta(days=1)
                erev_sunset = _sunset_for(lat, lon, tz, erev)
                last_day_sunset = _sunset_for(lat, lon, tz, last_day)

                window_start = erev_sunset - timedelta(minutes=candle_lighting_minutes(tz))
                window_end = last_day_sunset + timedelta(minutes=AFTER_SHEKIA_MINUTES)

                windows.append({
                    "name": f"{name} {hebrew_year}",
                    "start_datetime": window_start.isoformat(),
                    "end_datetime": window_end.isoformat(),
                    "is_blackout": True,
                    "affected_stations": {"station_ids": station_ids},
                    "reason": name,
                })
            except Exception as e:
                logger.warning(f"Could not compute {name} for Hebrew year {hebrew_year}: {e}")

    return windows


def merge_overlapping_windows(windows: list[dict]) -> list[dict]:
    """Merge windows that overlap in time (e.g., Yom Tov flowing into Shabbos).

    Windows are merged if end of one >= start of next. Names are joined with " / ".
    """
    if not windows:
        return []

    # Parse datetimes for sorting
    parsed = []
    for w in windows:
        start = datetime.fromisoformat(w["start_datetime"])
        end = datetime.fromisoformat(w["end_datetime"])
        parsed.append((start, end, w))

    parsed.sort(key=lambda x: x[0])

    def _pick_reason(reasons: list[str]) -> str:
        """Pick the most specific reason when merging (Yom Tov name over Shabbos)."""
        non_shabbos = [r for r in reasons if r != "Shabbos"]
        return non_shabbos[0] if non_shabbos else "Shabbos"

    merged = []
    cur_start, cur_end, cur_window = parsed[0]
    cur_names = [cur_window["name"]]
    cur_reasons = [cur_window.get("reason", "Manual")]

    for start, end, window in parsed[1:]:
        if start <= cur_end:
            # Overlapping — extend and merge names
            cur_end = max(cur_end, end)
            cur_names.append(window["name"])
            cur_reasons.append(window.get("reason", "Manual"))
        else:
            # No overlap — finalize current and start new
            merged.append({
                **cur_window,
                "name": " / ".join(cur_names),
                "start_datetime": cur_start.isoformat(),
                "end_datetime": cur_end.isoformat(),
                "reason": _pick_reason(cur_reasons),
            })
            cur_start, cur_end, cur_window = start, end, window
            cur_names = [window["name"]]
            cur_reasons = [window.get("reason", "Manual")]

    # Finalize last window
    merged.append({
        **cur_window,
        "name": " / ".join(cur_names),
        "start_datetime": cur_start.isoformat(),
        "end_datetime": cur_end.isoformat(),
        "reason": _pick_reason(cur_reasons),
    })

    return merged
