"""Song request service — fuzzy matching, auto-approve, and queue insertion."""

import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, fit_for_random_pick
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


_BY_SPLIT = re.compile(r"^(?P<title>.+)\s+by\s+(?P<artist>\S.*)$", re.IGNORECASE)


def split_title_artist(song_title: str, song_artist: str | None) -> tuple[str, str | None]:
    """Listeners often type "One Day by Matisyahu" into the title box and leave
    the artist empty. Split that into title + artist so the artist part is not
    scored as part of the title (which made such requests never match)."""
    if song_artist and song_artist.strip():
        return song_title, song_artist
    m = _BY_SPLIT.match(song_title.strip())
    if not m:
        return song_title, song_artist
    return m.group("title").strip(), m.group("artist").strip()


_DASH_SPLIT = re.compile(r"^(?P<a>.+?)\s+[-\u2013\u2014]\s+(?P<b>\S.*)$")


def request_readings(song_title: str, song_artist: str | None, known_artists: set[str]) -> set[tuple[str, str | None]]:
    """Ways to read a request as (title, artist).

    As typed; "X by Y"; "X - Y" either way round (listeners write "Song - Singer"
    and "Singer - Song"); and, with no separator, a known artist's name at the
    start or end of the title box ("matisyahu one day"). Only artist names already
    in the library are tried for the last one, so a request costs a few readings,
    not one per word."""
    readings = {(song_title, song_artist), split_title_artist(song_title, song_artist)}
    if song_artist and song_artist.strip():
        return readings
    text = song_title.strip()
    m = _DASH_SPLIT.match(text)
    if m:
        a, b = m.group("a").strip(), m.group("b").strip()
        readings |= {(a, b), (b, a)}
        return readings
    words = _normalize(text).split()
    for k in range(1, len(words)):
        head, tail = " ".join(words[:k]), " ".join(words[k:])
        if head in known_artists:
            readings.add((tail, head))
        if tail in known_artists:
            readings.add((head, tail))
    return readings


def _score_text(norm_title: str, norm_artist: str, title: str, artist: str, beat: float = 0.0) -> float:
    """Title x 0.7 + artist x 0.3 on normalized text. Returns 0 early when even the
    upper bound (SequenceMatcher.quick_ratio) cannot beat `beat`."""
    t_sm = SequenceMatcher(None, norm_title, title)
    if norm_artist and artist:
        a_sm = SequenceMatcher(None, norm_artist, artist)
        if t_sm.quick_ratio() * 0.7 + a_sm.quick_ratio() * 0.3 <= beat:
            return 0.0
        artist_score = a_sm.ratio()
    else:
        a_sm = None
        artist_score = 1.0 if not norm_artist and not artist else 0.0  # both empty: no penalty
        if t_sm.quick_ratio() * 0.7 + artist_score * 0.3 <= beat:
            return 0.0
    return t_sm.ratio() * 0.7 + artist_score * 0.3


def _score(norm_title: str, norm_artist: str, asset: Asset) -> float:
    title_score = SequenceMatcher(None, norm_title, _normalize(asset.title)).ratio()
    if norm_artist and asset.artist:
        artist_score = SequenceMatcher(None, norm_artist, _normalize(asset.artist)).ratio()
    elif not norm_artist and not asset.artist:
        artist_score = 1.0  # both empty — don't penalize
    else:
        artist_score = 0.0
    return title_score * 0.7 + artist_score * 0.3


async def fuzzy_match_asset(
    db: AsyncSession,
    song_title: str,
    song_artist: str | None,
    station_id: str,
) -> tuple[Asset | None, float]:
    """Find the best-matching music asset for a song request.

    Returns (best_asset, confidence) where confidence >= 0.6 means a match.
    Title weighted 70%, artist 30%.
    """
    # Only songs an automatic pick may air: never do-not-play, test items,
    # placeholders, block-only recordings or rejected songs. Only the columns the
    # score needs are read (the library has ~23,000 songs; loading whole rows took
    # about a second per request), and only the winner is loaded in full.
    result = await db.execute(
        select(Asset.id, Asset.title, Asset.artist).where(Asset.asset_type == "music", fit_for_random_pick())
    )
    rows = result.all()
    if not rows:
        return None, 0.0

    # Score every reading of the request (as typed, "X by Y", "X - Y" both ways,
    # a known artist before or after the title) and keep the best: "Stand By Me"
    # still works because the as-typed reading is always scored too.
    known_artists = {_normalize(r.artist) for r in rows if r.artist}
    readings = request_readings(song_title, song_artist, known_artists)
    norm_readings = [
        (_normalize(t), _normalize(a) if a else "") for t, a in readings
    ]

    best_id = None
    best_score = 0.0

    for row in rows:
        title = _normalize(row.title or "")
        artist = _normalize(row.artist) if row.artist else ""
        for nt, na in norm_readings:
            score = _score_text(nt, na, title, artist, best_score)
            if score > best_score:
                best_score = score
                best_id = row.id

    if best_id is not None and best_score >= 0.6:
        return await db.get(Asset, best_id), best_score
    return None, 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def station_day_start(db: AsyncSession, station_id, now: datetime | None = None) -> datetime:
    """Midnight of the station's own day, in UTC. Daily request caps count from here:
    counting from UTC midnight reset a New York station's day at 7-8 PM local."""
    from zoneinfo import ZoneInfo

    from app.models.station import Station

    now = now or _now()
    tz_name = None
    try:
        tz_name = (await db.execute(
            select(Station.timezone).where(Station.id == uuid.UUID(str(station_id)))
        )).scalar_one_or_none()
    except (ValueError, TypeError):
        pass
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = timezone.utc
    local_midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


async def check_auto_approve(
    db: AsyncSession,
    asset: Asset,
    station_id: str,
) -> bool:
    """Check if a matched asset can be auto-approved.

    Reads auto_approve_requests (bool) and max_requests_per_day (int)
    from asset.metadata_extra. Counts today's plays + pending/playing queue entries
    (a queued request is one of those).
    """
    extra = asset.metadata_extra or {}
    if not extra.get("auto_approve_requests", False):
        return False

    max_per_day = extra.get("max_requests_per_day", 3)
    today_start = await station_day_start(db, station_id)

    # Count today's PlayLog entries for this asset+station
    play_count_result = await db.execute(
        select(func.count(PlayLog.id)).where(
            PlayLog.station_id == station_id,
            PlayLog.asset_id == asset.id,
            PlayLog.start_utc >= today_start,
        )
    )
    play_count = play_count_result.scalar() or 0

    # Count pending/playing queue entries
    queue_count_result = await db.execute(
        select(func.count(QueueEntry.id)).where(
            QueueEntry.station_id == station_id,
            QueueEntry.asset_id == asset.id,
            QueueEntry.status.in_(["pending", "playing"]),
        )
    )
    queue_count = queue_count_result.scalar() or 0

    # A request that was queued is already counted above, as its queue entry (and once
    # played, in the play log): counting the request row as well counted it twice, so a
    # song allowed 2 a day took only 1 request (B-SONGREQUES-3).
    total = play_count + queue_count
    return total < max_per_day


async def add_to_queue(
    db: AsyncSession,
    asset_id: str,
    station_id: str,
) -> int:
    """Insert request after the last request-sourced entry in the queue.

    If no request entries exist, insert at position 1 (right after now-playing).
    Bumps positions of entries that come after the insertion point.
    Returns the new entry's position.
    """
    from sqlalchemy import update

    # Callers pass ids as strings; the columns are UUIDs.
    station_id = uuid.UUID(str(station_id))
    asset_id = uuid.UUID(str(asset_id))

    # Find the highest position among request-sourced pending entries
    result = await db.execute(
        select(func.max(QueueEntry.position)).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == "request",
        )
    )
    last_request_pos = result.scalar()

    if last_request_pos is not None:
        insert_pos = last_request_pos + 1
    else:
        # No request entries — insert at position 1 (right after now-playing)
        insert_pos = 1

    # Bump positions of all pending entries at or after insert_pos
    await db.execute(
        update(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.position >= insert_pos,
        )
        .values(position=QueueEntry.position + 1)
    )

    entry = QueueEntry(
        id=uuid.uuid4(),
        station_id=station_id,
        asset_id=asset_id,
        position=insert_pos,
        status="pending",
        source="request",
    )
    db.add(entry)
    await db.flush()
    return entry.position


async def get_queue_position_info(
    db: AsyncSession,
    station_id: str,
    position: int,
) -> dict:
    """Return estimated wait info for a queue position.

    Returns {songs_ahead, estimated_wait_minutes}.
    """
    # Count pending+playing entries ahead of this position
    result = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status.in_(["pending", "playing"]),
            QueueEntry.position < position,
        )
        .order_by(QueueEntry.position)
    )
    entries_ahead = result.scalars().all()

    songs_ahead = len(entries_ahead)
    total_seconds = 0.0
    for entry in entries_ahead:
        # Eagerly loaded asset may not be available; use default duration
        if entry.asset and entry.asset.duration:
            total_seconds += entry.asset.duration
        else:
            total_seconds += 180  # 3 min default

    return {
        "songs_ahead": songs_ahead,
        "estimated_wait_minutes": round(total_seconds / 60, 1),
    }
