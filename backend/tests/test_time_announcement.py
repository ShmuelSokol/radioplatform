from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.weather_spot_service import _build_time_text, _slot_time

EASTERN = ZoneInfo("America/New_York")


def test_time_text_uses_slot_hour_not_generation_time():
    generated_at = datetime(2026, 11, 15, 14, 37, tzinfo=EASTERN)
    slot = _slot_time("2026-11-17T09", generated_at)
    assert (slot.hour, slot.minute) == (9, 0)
    assert _build_time_text(slot) == "The time is 9:00 AM on Kohl Baramah."


def test_unparseable_slot_falls_back_to_now():
    now = datetime(2026, 11, 15, 14, 37, tzinfo=EASTERN)
    assert _slot_time("manual", now) == now
