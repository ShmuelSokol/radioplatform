"""Shabbos times match the published calendars.

Shmuel 2026-11-20: "Isn't 4:02 candle lighting in Jerusalem?" KBR Jerusalem went silent at
3:57 PM: 40 minutes before sea-level sunset (16:37). Jerusalem's calendars take sunset as
seen from the city's height (16:42), so candle lighting is 4:02. The expected times below
are Hebcal's (@hebcal/core, Jerusalem with elevation; Lakewood at sea level, 18 minutes).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.shabbos_service import generate_shabbos_windows

JERUSALEM = (31.7683, 35.2137, "Asia/Jerusalem")
LAKEWOOD = (40.0821, -74.2097, "America/New_York")


def _start(place, friday: date) -> datetime:
    lat, lon, tz = place
    w = generate_shabbos_windows(lat, lon, tz, friday, friday, ["s"])[0]
    return datetime.fromisoformat(w["start_datetime"]).astimezone(ZoneInfo(tz))


@pytest.mark.parametrize("friday, luach", [
    (date(2026, 11, 20), "16:02:00"),
    (date(2026, 11, 27), "16:00:00"),
    (date(2026, 12, 4), "15:59:00"),
])
def test_jerusalem_candle_lighting_is_the_luach_minute(friday, luach):
    assert _start(JERUSALEM, friday).strftime("%H:%M:%S") == luach


def test_jerusalem_motzei_shabbos_is_72_minutes_after_the_same_sunset():
    lat, lon, tz = JERUSALEM
    w = generate_shabbos_windows(lat, lon, tz, date(2026, 11, 20), date(2026, 11, 20), ["s"])[0]
    end = datetime.fromisoformat(w["end_datetime"]).astimezone(ZoneInfo(tz))
    # sunset seen from Jerusalem 16:41:4x on 2026-11-21 (sea level 16:36:51)
    assert end.strftime("%H:%M") == "17:53"


def test_lakewood_stays_at_sea_level():
    start = _start(LAKEWOOD, date(2026, 11, 20))
    assert start.strftime("%H:%M") == "16:18"
