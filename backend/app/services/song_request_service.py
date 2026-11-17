"""Song request service — fuzzy matching, auto-approve, and queue insertion."""

import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.song_request import SongRequest, RequestStatus


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


_SPLITS = re.compile(r"\s+(?:by|-|–|—|/)\s+", re.IGNORECASE)


def _guesses(song_title: str, song_artist: str | None) -> list[tuple[str, str]]:
    title = song_title or ""
    artist = song_artist or ""
    out = [(_normalize(title), _normalize(artist))]
    if not artist.strip():
        parts = _SPLITS.split(title, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            left, right = _normalize(parts[0]), _normalize(parts[1])
            out += [(left, right), (right, left)]
    return out


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
    result = await db.execute(
        select(Asset).where(Asset.asset_type == "music")
    )
    assets = result.scalars().all()
    if not assets:
        return None, 0.0

    # What the listener might mean: their title and artist as typed, and, when the
    # artist box is empty, the title split at " by " or " - " (both ways round): a
    # request "One day by matisyahu" with no artist scored 0.35 against "One Day".
    guesses = _guesses(song_title, song_artist)
    whole = _normalize(f"{song_title} {song_artist or ''}")

    best_asset = None
    best_score = 0.0

    for asset in assets:
        asset_title = _normalize(asset.title)
        asset_artist = _normalize(asset.artist) if asset.artist else ""
        score = 0.0
        for norm_title, norm_artist in guesses:
            title_score = SequenceMatcher(None, norm_title, asset_title).ratio()
            if norm_artist and asset_artist:
                artist_score = SequenceMatcher(None, norm_artist, asset_artist).ratio()
            elif not norm_artist and not asset_artist:
                artist_score = 1.0  # both empty — don't penalize
            else:
                artist_score = 0.0
            score = max(score, title_score * 0.7 + artist_score * 0.3)
        # Everything typed against "title artist" (and "artist title"), for requests
        # that put both in one box in any order
        if asset_artist:
            for both in (f"{asset_title} {asset_artist}", f"{asset_artist} {asset_title}"):
                score = max(score, SequenceMatcher(None, whole, both).ratio())

        if score > best_score:
            best_score = score
            best_asset = asset

    if best_score >= 0.6:
        return best_asset, best_score
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
