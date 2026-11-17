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
from app.models.song_request import SongRequest, RequestStatus


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

    # Score the request as typed and, if the title reads "X by Y", also as
    # title X + artist Y; keep the better one ("Stand By Me" still works).
    readings = {(song_title, song_artist), split_title_artist(song_title, song_artist)}
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


async def check_auto_approve(
    db: AsyncSession,
    asset: Asset,
    station_id: str,
) -> bool:
    """Check if a matched asset can be auto-approved.

    Reads auto_approve_requests (bool) and max_requests_per_day (int)
    from asset.metadata_extra. Counts today's plays + pending queue entries
    + approved/queued song requests.
    """
    extra = asset.metadata_extra or {}
    if not extra.get("auto_approve_requests", False):
        return False

    max_per_day = extra.get("max_requests_per_day", 3)
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

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

    # Count approved/queued song requests today
    req_count_result = await db.execute(
        select(func.count(SongRequest.id)).where(
            SongRequest.station_id == station_id,
            SongRequest.asset_id == asset.id,
            SongRequest.status.in_(
                [RequestStatus.APPROVED, RequestStatus.QUEUED]
            ),
            SongRequest.created_at >= today_start,
        )
    )
    req_count = req_count_result.scalar() or 0

    total = play_count + queue_count + req_count
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
