"""Song request service — fuzzy matching, auto-approve, and queue insertion."""

import re
import uuid
from datetime import datetime, timezone, timedelta
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

    norm_title = _normalize(song_title)
    norm_artist = _normalize(song_artist) if song_artist else ""

    best_asset = None
    best_score = 0.0

    for asset in assets:
        title_score = SequenceMatcher(
            None, norm_title, _normalize(asset.title)
        ).ratio()

        if norm_artist and asset.artist:
            artist_score = SequenceMatcher(
                None, norm_artist, _normalize(asset.artist)
            ).ratio()
        elif not norm_artist and not asset.artist:
            artist_score = 1.0  # both empty — don't penalize
        else:
            artist_score = 0.0

        score = title_score * 0.7 + artist_score * 0.3

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
    """Insert asset at end of pending queue. Returns the position number."""
    result = await db.execute(
        select(func.max(QueueEntry.position)).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
        )
    )
    max_pos = result.scalar() or 0
    entry = QueueEntry(
        id=uuid.uuid4(),
        station_id=station_id,
        asset_id=asset_id,
        position=max_pos + 1,
        status="pending",
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
