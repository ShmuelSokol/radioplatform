"""The last minutes before Shabbos: a song back-timed to end exactly at candle lighting,
then an item right at candle lighting (the shofar), then the Shabbos silence.

Shmuel, 2026-11-19: "It's Time to Say Good Shabbos" is the last song before candle
lighting each Shabbos and ends exactly at candle lighting, followed by the shofar.

Per station, `automation_config.shabbos_ending`:

    {"enabled": true,
     "song_asset_id": "<id>",      # ends exactly at the blackout's start
     "after_asset_id": "<id>",     # optional: airs at the blackout's start, before the silence
     "candle_items": {"2026-11-27": "<id>"},  # optional: that Friday's candle lighting
                                   # announcement (a new recording each week, keyed by the
                                   # blackout's local date); it ends exactly at candle
                                   # lighting and the song ends where it starts
     "candle_every_week": "<id>",  # optional: the same item every week (a sign-off with
                                   # no times in it); a dated entry wins for its week
     "shabbos": true,              # Erev Shabbos (default true)
     "yom_tov": false}             # Erev Yom Tov too (default false)

The blackout's start is candle lighting (shabbos_service: sunset - 18, or - 40 in
Israel), so the song ends where the silence would begin. When that week has a candle
lighting item (Main, following 107.9, coordinator 2026-11-22), the item is last and ends
at candle lighting, and the song is back-timed to end where the item starts. A week with
no item keeps the song as the last thing.

How it airs: the song is a hard timed entry at `start - its played length`; the
item after it is a hard timed entry at `start`, placed first in the blackout's own
silence group (same time, lower position), so the engine plays it and then the
silence. Timed items that would run into the song (a time check's sponsorships, a
station ID, a soft break) are taken out of the way while there is still time; the
group's first items (the time check itself) are kept when they end before the song.

Every scheduler pass calls `reconcile`; it changes nothing from SETTLE_SECONDS before
the song on (the engine has been handed it by then).
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.asset import Asset, stored_analysis
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry, is_soft_timed

logger = logging.getLogger(__name__)

SOURCE = "shabbos_end"
# How far ahead the ending is queued (the blackout's silence is filled 24 h ahead)
HORIZON = timedelta(hours=6)
# From this long before the song nothing is changed: the engine is handed timed
# items 45 s ahead (playout_engine.HAND_OVER), and an entry it holds cannot be moved
SETTLE_SECONDS = 60.0
# Timed items before the song must be over this long before it starts, so a group
# that runs a little long does not push the song (timed_groups) or get cut by it
CLEAR_SECONDS = 5.0
# Soft items due this close before the song would wait behind the song on air and
# then land inside the silence: they are taken out
SOFT_CLEAR = timedelta(minutes=5)
# ... and are moved this long before the song, so a whole song on air plus the break
# itself still ends before the song starts
SOFT_MOVE = timedelta(minutes=10)

YOM_TOV_REASONS = {
    "Rosh Hashanah", "Yom Kippur", "Sukkot", "Shemini Atzeret / Simchat Torah",
    "Pesach (First Days)", "Pesach (Last Days)", "Shavuot",
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def settings_of(config: dict | None) -> dict | None:
    """The station's ending settings when it is on and names a song, else None."""
    raw = (config or {}).get("shabbos_ending")
    if not isinstance(raw, dict) or not raw.get("enabled") or not raw.get("song_asset_id"):
        return None
    return {
        "song_asset_id": str(raw["song_asset_id"]),
        "after_asset_id": str(raw["after_asset_id"]) if raw.get("after_asset_id") else None,
        "candle_items": {
            str(k): str(v) for k, v in (raw.get("candle_items") or {}).items() if v
        } if isinstance(raw.get("candle_items"), dict) else {},
        "candle_every_week": str(raw["candle_every_week"]) if raw.get("candle_every_week") else None,
        "shabbos": raw.get("shabbos", True) is not False,
        "yom_tov": bool(raw.get("yom_tov", False)),
    }


def played_seconds(asset: Asset) -> float:
    """How long the item is on air: from its analysed start to its analysed end (the
    engine cues both), else its file length."""
    analysis = stored_analysis(asset.metadata_extra)
    cue_in = analysis.get("cue_in_seconds")
    cue_out = analysis.get("cue_out_seconds")
    end = float(cue_out) if isinstance(cue_out, (int, float)) and cue_out > 0 else float(asset.duration or 0)
    start = float(cue_in) if isinstance(cue_in, (int, float)) and cue_in > 0 else 0.0
    return max(0.0, end - start)


def window_kind(window: HolidayWindow, tz: str) -> set[str]:
    """'shabbos' when the window starts on a Friday (station time) as Shabbos or a Yom
    Tov running into Shabbos; 'yom_tov' when it is a Yom Tov window."""
    reason = window.reason or ""
    kinds: set[str] = set()
    is_yom_tov = reason in YOM_TOV_REASONS
    try:
        local = _aware(window.start_datetime).astimezone(ZoneInfo(tz or "UTC"))
    except Exception:
        local = _aware(window.start_datetime)
    if reason == "Shabbos" or (is_yom_tov and local.weekday() == 4):
        kinds.add("shabbos")
    if is_yom_tov:
        kinds.add("yom_tov")
    return kinds


def wants(window: HolidayWindow, tz: str, cfg: dict) -> bool:
    kinds = window_kind(window, tz)
    return ("shabbos" in kinds and cfg["shabbos"]) or ("yom_tov" in kinds and cfg["yom_tov"])


async def _next_window(db: AsyncSession, station, now: datetime, cfg: dict) -> HolidayWindow | None:
    from app.services.station_service import window_covers_station

    res = await db.execute(
        select(HolidayWindow)
        .where(
            HolidayWindow.is_blackout == True,  # noqa: E712
            HolidayWindow.start_datetime > now,
            HolidayWindow.start_datetime <= now + HORIZON,
        )
        .order_by(HolidayWindow.start_datetime)
    )
    for w in res.scalars().all():
        if window_covers_station(w, station.id) and wants(w, station.timezone, cfg):
            return w
    return None


def window_date(window: HolidayWindow, tz: str) -> str:
    """The blackout's start date in station time (the key of `candle_items`)."""
    try:
        return _aware(window.start_datetime).astimezone(ZoneInfo(tz or "UTC")).date().isoformat()
    except Exception:
        return _aware(window.start_datetime).date().isoformat()


def plan(
    window_start: datetime, song: Asset | None, after: Asset | None, candle: Asset | None = None
) -> list[tuple[Asset, datetime]]:
    """(item, time) pairs, earliest first: the song, then (when there is one) the candle
    lighting item ending at the window's start, the song ending where it starts; the
    item after them at the start."""
    start = _aware(window_start)
    out: list[tuple[Asset, datetime]] = []
    end = start
    candle_at = None
    if candle is not None and played_seconds(candle) > 0:
        candle_at = start - timedelta(seconds=played_seconds(candle))
        end = candle_at
    if song is not None:
        out.append((song, end - timedelta(seconds=played_seconds(song))))
    if candle_at is not None:
        out.append((candle, candle_at))
    if after is not None:
        out.append((after, start))
    return out


async def _asset(db: AsyncSession, asset_id: str | None) -> Asset | None:
    if not asset_id:
        return None
    try:
        key = uuid.UUID(str(asset_id))
    except ValueError:
        return None
    return (await db.execute(select(Asset).where(Asset.id == key))).scalar_one_or_none()


def _may_air(asset: Asset) -> bool:
    """Not do-not-play, not a placeholder, not rejected in review (models.asset.not_placeholder)."""
    from app.models.asset import NEVER_AUTO_CATEGORIES, NEVER_AUTO_TITLES

    if (getattr(asset, "review_status", None) or "") == "rejected":
        return False
    if (asset.category or "").strip().lower() in NEVER_AUTO_CATEGORIES:
        return False
    return (asset.title or "").strip().lower() not in NEVER_AUTO_TITLES


async def _ours(db: AsyncSession, station_id) -> list[QueueEntry]:
    res = await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == SOURCE,
        )
    )
    return list(res.scalars().all())


async def reconcile(db: AsyncSession, station, now: datetime | None = None) -> dict:
    """Queue (or correct, or remove) the ending for the station's next blackout. Commits
    when it changed anything."""
    now = _aware(now or datetime.now(timezone.utc))
    cfg = settings_of(station.automation_config)
    ours = await _ours(db, station.id)
    settle = now + timedelta(seconds=SETTLE_SECONDS)
    changed = {"added": 0, "removed": 0, "cleared": 0}

    window = await _next_window(db, station, now, cfg) if cfg else None
    wanted: list[tuple[Asset, datetime]] = []
    if window is not None:
        song = await _asset(db, cfg["song_asset_id"])
        after = await _asset(db, cfg["after_asset_id"])
        candle = await _asset(
            db, cfg["candle_items"].get(window_date(window, station.timezone)) or cfg["candle_every_week"]
        )
        if candle is not None and not _may_air(candle):
            # Taken off the air or rejected: nothing placeholder or unapproved airs,
            # the week ends with the song as before
            logger.warning("Shabbos ending on station %s: candle lighting item %s may not air", station.id, candle.id)
            candle = None
        if song is None:
            logger.warning("Shabbos ending on station %s: song %s not in the library", station.id, cfg["song_asset_id"])
        _analyse_soon(song)
        _analyse_soon(after)
        _analyse_soon(candle)
        wanted = plan(window.start_datetime, song, after, candle)
        if wanted and wanted[0][1] <= settle:
            # The ending is already handed over (or about to be): leave it as it is
            return changed

    # Entries that no longer match what is wanted go (a changed setting, a new song
    # length, a regenerated window); entries already settled stay
    keep: dict[tuple[str, datetime], QueueEntry] = {}
    for e in ours:
        at = _aware(e.preempt_at) if e.preempt_at else None
        if at is not None and at <= settle:
            continue
        key = (str(e.asset_id), at)
        if any(str(a.id) == key[0] and abs((t - at).total_seconds()) < 0.01 for a, t in wanted) and key not in keep:
            keep[key] = e
            continue
        await db.delete(e)
        changed["removed"] += 1

    if not wanted:
        if changed["removed"]:
            await db.commit()
        return changed

    from sqlalchemy import func

    top = (
        await db.execute(
            select(func.coalesce(func.max(QueueEntry.position), 0)).where(
                QueueEntry.station_id == station.id, QueueEntry.status.in_(["pending", "playing"])
            )
        )
    ).scalar() or 0
    for asset, at in wanted:
        if any(k[0] == str(asset.id) and abs((k[1] - at).total_seconds()) < 0.01 for k in keep):
            continue
        db.add(QueueEntry(
            id=uuid.uuid4(),
            station_id=station.id,
            asset_id=asset.id,
            position=top + 1 + changed["added"],
            status="pending",
            source=SOURCE,
            preempt_at=at,
            timing_mode="hard",
        ))
        changed["added"] += 1
    await db.flush()

    song_at = wanted[0][1]
    window_start = _aware(window.start_datetime)
    changed["removed"] += await _song_not_earlier(db, station.id, cfg["song_asset_id"])
    changed["cleared"] = await _clear_the_way(db, station.id, song_at, window_start, now)
    await _after_item_first(db, station.id, window_start)
    if any(changed.values()):
        await db.commit()
        logger.info(
            "Shabbos ending on station %s for %s: song at %s, +%d -%d, %d timed items cleared",
            station.id, window.name, song_at.isoformat(), changed["added"], changed["removed"], changed["cleared"],
        )
    else:
        await db.commit()
    return changed


async def _song_not_earlier(db: AsyncSession, station_id, song_id: str) -> int:
    """The song is a top hit and can come up in rotation: not in the hours before it
    ends the week. Its waiting copies go, except one the engine already holds (it is on
    its way to air) and, on the browser player, the very next item."""
    from app.config import settings
    from app.services.playout_engine import held_entry_ids

    engine = settings.engine_drives(station_id)
    held = held_entry_ids(station_id) if engine else set()
    res = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.is_(None),
        )
        .order_by(QueueEntry.position)
    )
    removed = 0
    for i, e in enumerate(res.scalars().all()):
        if str(e.asset_id) != song_id or e.id in held or str(e.id) in {str(h) for h in held}:
            continue
        if not engine and i == 0:
            continue
        await db.delete(e)
        removed += 1
    if removed:
        await db.flush()
    return removed


def _length(entry: QueueEntry) -> float:
    from app.services.timed_groups import _length as group_length

    return group_length(entry)


async def _clear_the_way(db: AsyncSession, station_id, song_at: datetime, window_start: datetime, now: datetime) -> int:
    """Take out timed items that would run into the song or air between it and the
    silence. A hard group that starts before the song keeps its first items while they
    end CLEAR_SECONDS before the song; the rest of it goes. A soft break due in the last
    SOFT_CLEAR before the song is moved SOFT_MOVE before it. Returns how many it moved
    or removed."""
    from app.services.timed_groups import _is_silence

    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at > now + timedelta(seconds=SETTLE_SECONDS),
            QueueEntry.preempt_at < window_start,
            QueueEntry.source != SOURCE,
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )
    entries = [e for e in res.unique().scalars().all() if not _is_silence(e)]
    removed = 0
    groups: dict[datetime, list[QueueEntry]] = {}
    for e in entries:
        groups.setdefault(_aware(e.preempt_at), []).append(e)
    limit = song_at - timedelta(seconds=CLEAR_SECONDS)
    for at, items in groups.items():
        soft = all(is_soft_timed(e) for e in items)
        if soft:
            if at >= song_at - SOFT_CLEAR:
                # A break that would wait behind the song on air and then run into the
                # Shabbos song: it airs earlier instead (after the song on air then), or,
                # with no time left for that, not at all
                target = song_at - SOFT_MOVE
                for e in items:
                    if target > now + timedelta(seconds=SETTLE_SECONDS):
                        e.preempt_at = target
                        e.hard_by = None
                    else:
                        await db.delete(e)
                    removed += 1
            continue
        end = at
        for e in items:
            if at >= limit or end + timedelta(seconds=_length(e)) > limit:
                await db.delete(e)
                removed += 1
            else:
                end += timedelta(seconds=_length(e))
    if removed:
        await db.flush()
    return removed


async def _after_item_first(db: AsyncSession, station_id, window_start: datetime) -> None:
    """The item at candle lighting goes before the silence queued for the same moment
    (the engine plays a group in position order)."""
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.preempt_at == window_start,
        )
        .order_by(QueueEntry.position)
    )
    group = list(res.unique().scalars().all())
    mine = [e for e in group if e.source == SOURCE]
    others = [e for e in group if e.source != SOURCE]
    if not mine or not others:
        return
    first = min(e.position for e in others)
    if all(e.position < first for e in mine):
        return
    # Make room just before the silence: everything from `first` on moves up
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position >= first,
            QueueEntry.source != SOURCE,
        )
        .values(position=QueueEntry.position + len(mine))
    )
    for i, e in enumerate(mine):
        e.position = first + i
    await db.flush()


def _analyse_soon(asset: Asset | None) -> None:
    """Measure the item's real start and end before it airs (its played length decides
    when the song starts)."""
    if asset is None:
        return
    try:
        from app.services import playout_engine

        playout_engine._level_job(asset)
    except RuntimeError:
        pass  # no running loop (scripts)
    except Exception:
        logger.debug("Shabbos ending: analysis of %s not started", asset.id, exc_info=True)
