"""What a hard timed item (the hourly time check, a hard block) does to a shiur or story.

Cutting a shiur 14 minutes in for the time check loses the rest of it (heard on
KBR Jerusalem). Each station chooses, in automation_config["timed_during_long_items"]:

  "pause" (default)  the time check plays, then the shiur carries on where it stopped
  "wait"             the time check waits until the shiur has finished
  "cut"              the time check cuts the shiur (the old behaviour)

Music and short items are always cut as before.
"""
from datetime import datetime, timedelta, timezone

POLICIES = ("pause", "wait", "cut")
DEFAULT_POLICY = "pause"
SETTING = "timed_during_long_items"

# Library types that are talks, not songs
LONG_FORM_TYPES = {"shiur", "shiurim", "story", "stories", "lecture", "program", "programme"}
# Anything this long counts too, whatever its type
LONG_FORM_MIN_SECONDS = 12 * 60
# A resumed item starts this much before where it was stopped, so the sentence that
# was fading out is heard again
RESUME_REWIND_SECONDS = 3.0
# Closer than this to its end, an item is not resumed (it just ends)
MIN_REMAINING_TO_RESUME = 30.0


def policy(station) -> str:
    config = getattr(station, "automation_config", None) or {}
    value = str(config.get(SETTING) or DEFAULT_POLICY).lower()
    return value if value in POLICIES else DEFAULT_POLICY


def is_long_form(asset) -> bool:
    if asset is None:
        return False
    kind = (getattr(asset, "asset_type", None) or "").strip().lower()
    if kind in LONG_FORM_TYPES:
        return True
    return (getattr(asset, "duration", None) or 0) >= LONG_FORM_MIN_SECONDS


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def elapsed(entry, now: datetime) -> float:
    if not entry.started_at:
        return 0.0
    return max(0.0, (now - _aware(entry.started_at)).total_seconds())


def resume_point(entry, now: datetime) -> float | None:
    """Where to carry on after the pause, or None when too little is left to bother."""
    duration = (entry.asset.duration if entry.asset else None) or 0.0
    at = elapsed(entry, now)
    if duration and duration - at < MIN_REMAINING_TO_RESUME:
        return None
    return max(0.0, at - RESUME_REWIND_SECONDS)


def ends_at(entry) -> datetime | None:
    if not entry.started_at:
        return None
    duration = (entry.asset.duration if entry.asset else None) or 180.0
    return _aware(entry.started_at) + timedelta(seconds=duration)
