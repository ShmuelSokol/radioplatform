"""What the station's RDS shows right now: PS name and RadioText.

The rotation is worked out from the clock alone (slot = seconds since epoch //
rotation_seconds), so every poll of the feed, from any server process, agrees on
the line without keeping state. A sponsor's spot on air can take over the line.
Hebrew has no place in the RDS character set, so it is dropped; a title that is
Hebrew only falls back to the station's line. See wiki/rds-feed.md.
"""
from __future__ import annotations

import logging
import re
import time as _time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.rds import RdsMessage, RdsPlay, RdsSettings
from app.models.sponsor import Sponsor
from app.models.station import Station

logger = logging.getLogger(__name__)

RADIOTEXT_MAX = 64
PS_MAX = 8
PLACEHOLDERS = ("time", "date", "day", "temp", "weather", "station")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
# Items that are station furniture, not something to name on the radio display.
UNNAMED_TYPES = {"jingle", "station_id", "time_announcement", "weather", "weather_spot", "silence", "blank"}

DEFAULTS = {
    "ps_text": None,
    "ps_mode": "static",
    "rotation_seconds": 15,
    "now_playing_weight": 2,
    "sponsor_while_spot_airs": True,
    "fallback_text": None,
}

_SWAPS = {"—": "-", "–": "-", "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "…": "...",
          "״": '"', "׳": "'", "ß": "ss", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L"}
_HEBREW_RE = re.compile(r"[֐-׿יִ-ﭏ]")


# ── text ────────────────────────────────────────────────────────────


def rds_safe(text: str | None) -> str:
    """One line of plain printable ASCII: punctuation folded, accents dropped
    (é -> e), Hebrew and anything else the RDS charset cannot show removed."""
    if not text:
        return ""
    for bad, good in _SWAPS.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKD", text)
    out = "".join(ch if (" " <= ch <= "~") else " " for ch in text if not unicodedata.combining(ch))
    out = " ".join(out.split())
    # A separator left dangling by a removed Hebrew half: "Artist - " -> "Artist"
    out = re.sub(r"^[\s\-:|/,.]+|[\s\-:|/,]+$", "", out)
    return out


def dropped_characters(text: str | None) -> list[str]:
    """Characters of `text` the RDS line will not show (for warnings in the editor)."""
    if not text:
        return []
    seen: list[str] = []
    for ch in text:
        if ch in _SWAPS or " " <= ch <= "~" or ch in seen:
            continue
        folded = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        if folded and all(" " <= c <= "~" for c in folded):
            continue
        if ch.isprintable():
            seen.append(ch)
    return seen


def has_hebrew(text: str | None) -> bool:
    return bool(text and _HEBREW_RE.search(text))


def fit(text: str, limit: int = RADIOTEXT_MAX) -> str:
    """Trim to `limit`, at a word break when one is near the end."""
    text = rds_safe(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit - 12:
        cut = cut[:space]
    return cut.rstrip(" -:,|/")


def fit_ps(text: str | None) -> str:
    return rds_safe(text)[:PS_MAX].rstrip()


def default_ps(station_name: str) -> str:
    """"Kol B'ramah Main" -> "KOLBRAMA"; a short name stays as it is."""
    name = rds_safe(station_name)
    if len(name) <= PS_MAX:
        return name
    letters = re.sub(r"[^A-Za-z0-9]", "", name).upper()
    return letters[:PS_MAX]


def song_line(asset: Asset | None) -> str:
    """"Artist - Title" for music and programs, the title alone for a sponsorship,
    "" for station furniture or a title with nothing the RDS charset can show."""
    if asset is None:
        return ""
    kind = (asset.asset_type or "").lower()
    category = (asset.category or "").lower()
    if kind in UNNAMED_TYPES or category in UNNAMED_TYPES:
        return ""
    extra = asset.metadata_extra if isinstance(asset.metadata_extra, dict) else {}
    override = extra.get("rds_text")
    if isinstance(override, str) and rds_safe(override):
        return fit(override)
    title = rds_safe(asset.title)
    artist = rds_safe(asset.artist)
    if kind == "spot":
        return fit(title)
    if title and artist:
        return fit(f"{artist} - {title}")
    return fit(title or artist)  # Hebrew-only title with a Latin artist: the artist alone


def render(template: str, values: dict[str, str]) -> str:
    """Fill {placeholders}. An unknown placeholder stays as typed; a known one
    with no value ("" weather when the weather service is down) makes the whole
    line empty, so the rotation skips it instead of showing a hole."""
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        key = m.group(1)
        if key not in PLACEHOLDERS:
            return m.group(0)
        val = values.get(key) or ""
        if not val:
            missing = True
        return val

    out = _PLACEHOLDER_RE.sub(sub, template or "")
    return "" if missing else fit(out)


def placeholders_in(template: str) -> list[str]:
    return [k for k in _PLACEHOLDER_RE.findall(template or "") if k in PLACEHOLDERS]


def clock_values(now: datetime, tz: str, station_name: str) -> dict[str, str]:
    local = now.astimezone(ZoneInfo(tz or "UTC"))
    hour = local.hour % 12 or 12
    return {
        "time": f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}",
        "date": f"{local.strftime('%B')} {local.day}",
        "day": local.strftime("%A"),
        "station": rds_safe(station_name),
    }


# ── schedule ────────────────────────────────────────────────────────


def runs_at(msg: RdsMessage, local: datetime) -> bool:
    """Is this message in the rotation at station-local time `local`?"""
    if not msg.is_active:
        return False
    today = local.date()
    if msg.start_date and today < msg.start_date:
        return False
    if msg.end_date and today > msg.end_date:
        return False
    days = {d.strip() for d in (msg.days_of_week or "").split(",") if d.strip()}
    start, end = msg.start_time, msg.end_time
    now_t = local.time().replace(tzinfo=None)
    weekday = local.weekday()
    if start and end and end <= start:
        # Runs past midnight: after start counts as today, before end as yesterday's run
        if now_t >= start:
            return str(weekday) in days
        if now_t < end:
            return str((weekday - 1) % 7) in days
        return False
    if str(weekday) not in days:
        return False
    if start and now_t < start:
        return False
    if end and now_t >= end:
        return False
    return True


def weighted_cycle(items: list[tuple[str, int]]) -> list[str]:
    """Smooth weighted round robin: weights 3/1 give a a b a, not a a a b."""
    items = [(k, w) for k, w in items if w > 0]
    total = sum(w for _, w in items)
    current = {k: 0 for k, _ in items}
    out: list[str] = []
    for _ in range(total):
        for k, w in items:
            current[k] += w
        best = max(items, key=lambda kw: current[kw[0]])[0]
        current[best] -= total
        out.append(best)
    return out


# ── state ───────────────────────────────────────────────────────────


@dataclass
class RdsState:
    ps: str
    rt: str
    kind: str  # now_playing | message | sponsor_on_air | fallback
    message_id: uuid.UUID | None = None
    sponsor_id: uuid.UUID | None = None
    sponsor_name: str | None = None
    changes_at: datetime | None = None
    slot: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ps": self.ps,
            "rt": self.rt,
            "kind": self.kind,
            "message_id": str(self.message_id) if self.message_id else None,
            "sponsor_id": str(self.sponsor_id) if self.sponsor_id else None,
            "sponsor_name": self.sponsor_name,
            "changes_at": self.changes_at.isoformat() if self.changes_at else None,
        }


@dataclass
class Context:
    station: Station
    settings: dict
    messages: list[RdsMessage]
    sponsors: dict[uuid.UUID, str]
    on_air: Asset | None
    weather: dict[str, str]


async def settings_for(db: AsyncSession, station_id: uuid.UUID) -> dict:
    row = (await db.execute(select(RdsSettings).where(RdsSettings.station_id == station_id))).scalar_one_or_none()
    out = dict(DEFAULTS)
    if row is not None:
        for k in DEFAULTS:
            out[k] = getattr(row, k)
    return out


async def messages_for(db: AsyncSession, station_id: uuid.UUID) -> list[RdsMessage]:
    rows = (
        await db.execute(
            select(RdsMessage)
            .where(or_(RdsMessage.station_id == station_id, RdsMessage.station_id.is_(None)))
            .order_by(RdsMessage.created_at, RdsMessage.id)
        )
    ).scalars().all()
    return list(rows)


async def on_air_asset(db: AsyncSession, station_id: uuid.UUID) -> Asset | None:
    return (
        await db.execute(
            select(Asset)
            .join(QueueEntry, QueueEntry.asset_id == Asset.id)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


_weather_cache: dict[uuid.UUID, tuple[float, dict[str, str]]] = {}
WEATHER_CACHE_SECONDS = 600


async def weather_values(station: Station) -> dict[str, str]:
    """{temp} and {weather} for the station; {} when the weather service is off or failing."""
    hit = _weather_cache.get(station.id)
    if hit and _time.monotonic() - hit[0] < WEATHER_CACHE_SECONDS:
        return hit[1]
    values: dict[str, str] = {}
    try:
        from app.services.weather_service import get_current_weather

        kwargs = {"timezone_name": station.timezone or "America/New_York"}
        if station.latitude is not None and station.longitude is not None:
            kwargs.update(lat=station.latitude, lon=station.longitude)
        w = await get_current_weather(**kwargs)
        values = {"temp": f"{w['temp_f']}F", "weather": str(w.get("description") or "")}
    except Exception as e:  # weather is a nice-to-have on the display
        logger.debug("RDS weather unavailable: %s", e)
    _weather_cache[station.id] = (_time.monotonic(), values)
    return values


async def load_context(db: AsyncSession, station: Station) -> Context:
    settings = await settings_for(db, station.id)
    messages = await messages_for(db, station.id)
    sponsor_ids = {m.sponsor_id for m in messages if m.sponsor_id}
    on_air = await on_air_asset(db, station.id)
    if on_air is not None and on_air.sponsor_id:
        sponsor_ids.add(on_air.sponsor_id)
    sponsors: dict[uuid.UUID, str] = {}
    if sponsor_ids:
        for sid, name in (await db.execute(select(Sponsor.id, Sponsor.name).where(Sponsor.id.in_(sponsor_ids)))).all():
            sponsors[uuid.UUID(str(sid))] = name
    needs_weather = any({"temp", "weather"} & set(placeholders_in(m.text)) for m in messages if m.is_active)
    weather = await weather_values(station) if needs_weather else {}
    return Context(station, settings, messages, sponsors, on_air, weather)


def _sid(value) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def compute(ctx: Context, now: datetime, *, include_on_air: bool = True, song_stand_in: str = "") -> RdsState:
    """The PS and RadioText at `now`. `include_on_air=False` (previews of later
    slots) leaves the spot override out, since nobody knows what airs then, and
    shows `song_stand_in` on now-playing turns."""
    s = ctx.settings
    station = ctx.station
    tz = station.timezone or "UTC"
    local = now.astimezone(ZoneInfo(tz))
    base_ps = fit_ps(s["ps_text"]) or default_ps(station.name)
    fallback = fit(s["fallback_text"] or "") or fit(station.name)
    rotation = max(5, min(int(s["rotation_seconds"] or 15), 600))
    epoch = int(now.timestamp())
    slot = epoch // rotation
    changes_at = datetime.fromtimestamp((slot + 1) * rotation, tz=UTC)
    values = {**clock_values(now, tz, station.name), **ctx.weather}

    on_air = ctx.on_air if include_on_air else None

    # A sponsor's spot on air takes the line for as long as it plays
    if on_air is not None and (on_air.asset_type or "").lower() == "spot" and s["sponsor_while_spot_airs"]:
        sponsor_id = _sid(on_air.sponsor_id)
        for m in ctx.messages:
            if m.is_active and m.sponsor_id and _sid(m.sponsor_id) == sponsor_id:
                text = render(m.text, values)
                if text:
                    ps = fit_ps(m.ps_text) if s["ps_mode"] == "rotate" and m.ps_text else base_ps
                    return RdsState(ps, text, "sponsor_on_air", m.id, sponsor_id,
                                    ctx.sponsors.get(sponsor_id), None, slot)
        line = song_line(on_air)
        if line:
            return RdsState(base_ps, line, "sponsor_on_air", None, sponsor_id,
                            ctx.sponsors.get(sponsor_id) if sponsor_id else None, None, slot)

    # Only lines with something to show take turns (a weather line while the weather
    # service is down, or the song turn during a jingle, would double the next line)
    song = song_line(on_air) if include_on_air else song_stand_in
    texts: dict[str, tuple[RdsMessage, str]] = {}
    for m in ctx.messages:
        if runs_at(m, local):
            text = render(m.text, values)
            if text:
                texts[str(m.id)] = (m, text)
    cycle_items: list[tuple[str, int]] = []
    if int(s["now_playing_weight"] or 0) > 0 and song:
        cycle_items.append(("now_playing", int(s["now_playing_weight"])))
    cycle_items += [(k, max(1, int(m.weight or 1))) for k, (m, _) in texts.items()]
    cycle = weighted_cycle(cycle_items)
    if cycle:
        key = cycle[slot % len(cycle)]
        if key == "now_playing":
            return RdsState(base_ps, song, "now_playing", None, None, None, changes_at, slot)
        m, text = texts[key]
        ps = fit_ps(m.ps_text) if s["ps_mode"] == "rotate" and m.ps_text else base_ps
        sponsor_id = _sid(m.sponsor_id)
        return RdsState(ps, text, "message", m.id, sponsor_id,
                        ctx.sponsors.get(sponsor_id) if sponsor_id else None, changes_at, slot)
    return RdsState(base_ps, fallback, "fallback", None, None, None, changes_at, slot)


async def current(db: AsyncSession, station: Station, now: datetime | None = None) -> RdsState:
    ctx = await load_context(db, station)
    return compute(ctx, now or datetime.now(UTC))


async def preview(db: AsyncSession, station: Station, minutes: int = 10, now: datetime | None = None) -> list[dict]:
    """The coming slots, one row each, with "now playing" turns shown as such."""
    now = now or datetime.now(UTC)
    ctx = await load_context(db, station)
    rotation = max(5, min(int(ctx.settings["rotation_seconds"] or 15), 600))
    rows = []
    first = compute(ctx, now)
    rows.append({"at": now.isoformat(), **first.as_dict()})
    t = first.changes_at or (now + timedelta(seconds=rotation))
    end = now + timedelta(minutes=minutes)
    while t < end and len(rows) < 200:
        st = compute(ctx, t, include_on_air=False, song_stand_in="(the song on air then)")
        rows.append({"at": t.isoformat(), **st.as_dict()})
        t = st.changes_at or (t + timedelta(seconds=rotation))
    return rows


# ── log ─────────────────────────────────────────────────────────────

# Per station: (key of the line last logged, monotonic time of the last write, play id)
_last_logged: dict[uuid.UUID, tuple[tuple, float, uuid.UUID]] = {}
LOG_REFRESH_SECONDS = 30
# A gap longer than this between fetches of the same line starts a new play row
SAME_PLAY_GAP = timedelta(minutes=3)


async def log_fetch(db: AsyncSession, station_id: uuid.UUID, state: RdsState, now: datetime | None = None) -> None:
    """Record that the encoder fetched `state`. One row per run of the same line;
    repeated polls only move last_seen_at, at most every LOG_REFRESH_SECONDS."""
    now = now or datetime.now(UTC)
    key = (state.kind, state.message_id, state.sponsor_id, state.rt, state.ps)
    mono = _time.monotonic()
    last = _last_logged.get(station_id)
    if last and last[0] == key and mono - last[1] < LOG_REFRESH_SECONDS:
        return
    try:
        row = None
        if last and last[0] == key:
            row = await db.get(RdsPlay, last[2])
            if row is not None:
                seen = row.last_seen_at if row.last_seen_at.tzinfo else row.last_seen_at.replace(tzinfo=UTC)
                if now - seen > SAME_PLAY_GAP:
                    row = None
        if row is None:
            row = RdsPlay(
                id=uuid.uuid4(), station_id=station_id, message_id=state.message_id, sponsor_id=state.sponsor_id,
                kind=state.kind, text=state.rt, ps_text=state.ps, first_seen_at=now, last_seen_at=now, fetches=1,
            )
            db.add(row)
        else:
            row.last_seen_at = now
            row.fetches = (row.fetches or 0) + 1
        await db.commit()
        _last_logged[station_id] = (key, mono, row.id)
    except Exception as e:  # the feed must answer even when the log cannot be written
        logger.warning("RDS play log write failed: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass


def check_text(text: str, *, ps_text: str | None = None, kind: str = "text", sponsor_name: str | None = None) -> dict:
    """Warnings for the editor: length, characters radios cannot show, and the
    sponsorship wording rules for sponsor lines."""
    warnings: list[str] = []
    errors: list[str] = []
    unknown = [k for k in _PLACEHOLDER_RE.findall(text or "") if k not in PLACEHOLDERS]
    if unknown:
        warnings.append("Not a placeholder, will show as typed: " + ", ".join("{" + k + "}" for k in unknown))
    sample = {"time": "12:45 PM", "date": "September 30", "day": "Wednesday",
              "temp": "100F", "weather": "scattered thunderstorms", "station": "Kol B'ramah - All Day Stories"}
    filled = _PLACEHOLDER_RE.sub(lambda m: sample.get(m.group(1), m.group(0)), text or "")
    safe = rds_safe(filled)
    if not safe:
        errors.append("Nothing left to show: RDS radios can only show Latin letters, digits and punctuation.")
    if len(safe) > RADIOTEXT_MAX:
        msg = f"{len(safe)} characters; radios show {RADIOTEXT_MAX}, so the end is cut: \"{fit(safe)}\""
        (warnings if placeholders_in(text) else errors).append(msg)
    dropped = dropped_characters(text)
    if dropped:
        warnings.append("Radios cannot show " + " ".join(dropped[:12])
                        + (" (Hebrew is dropped; write it in English letters)" if has_hebrew(text) else "")
                        + "; left out.")
    if ps_text is not None and ps_text != "":
        if len(ps_text) > PS_MAX:
            errors.append(f"Station name (PS) is {len(ps_text)} characters; the limit is {PS_MAX}.")
        if dropped_characters(ps_text):
            errors.append("Station name (PS) has characters radios cannot show.")
    review = None
    if kind == "sponsor":
        from app.services.copy_review_service import basic_review

        review = basic_review(filled, sponsor_name)
        for issue in review["issues"]:
            warnings.append(f"Sponsorship rules: {issue['problem']} {issue['suggestion']}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "example": fit(safe),
            "length": len(safe), "review": review}
