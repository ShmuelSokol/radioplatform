"""Dynamic "requested" category service.

Songs with N+ requests in the last 30 days are tagged as requested
(metadata_extra["requested"] = "yes"). They keep their own category, so they stay
in that category's rotation, and rotations built on the "requested" category pick
them too (see app.models.asset.in_category). The tag is removed when a song drops
below the threshold. The category itself is never written, so a staff edit is
never overwritten (Shmuel, 2026-11-17, checklist V2-114).

Songs swapped by the old version (category "requested", own category saved in
metadata_extra["original_category"]) get their own category back on the next refresh.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import REQUESTED_CATEGORY, REQUESTED_TAG, REQUESTED_TAG_VALUE, Asset
from app.models.song_request import SongRequest

logger = logging.getLogger(__name__)


async def refresh_requested_category(db: AsyncSession, threshold: int = 3) -> int:
    """Refresh the "requested" tag. Returns the number of songs now tagged."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    popular_q = (
        select(SongRequest.asset_id, func.count(SongRequest.id).label("cnt"))
        .where(
            SongRequest.asset_id.isnot(None),
            SongRequest.created_at >= cutoff,
        )
        .group_by(SongRequest.asset_id)
        .having(func.count(SongRequest.id) >= threshold)
    )
    popular_ids = {row[0] for row in (await db.execute(popular_q)).all()}

    # Every song that is popular, tagged, or still swapped by the old version
    conditions = [
        Asset.metadata_extra[REQUESTED_TAG].astext == REQUESTED_TAG_VALUE,
        Asset.category == REQUESTED_CATEGORY,
    ]
    if popular_ids:
        conditions.append(Asset.id.in_(popular_ids))
    assets = (await db.execute(select(Asset).where(or_(*conditions)))).scalars().all()

    tagged = untagged = healed = 0
    for asset in assets:
        extra = dict(asset.metadata_extra or {})
        changed = False
        if asset.category == REQUESTED_CATEGORY and "original_category" in extra:
            # Swapped by the old version: give the song its own category back
            asset.category = extra.pop("original_category")
            changed = True
            healed += 1
        if asset.id in popular_ids:
            if extra.get(REQUESTED_TAG) != REQUESTED_TAG_VALUE:
                extra[REQUESTED_TAG] = REQUESTED_TAG_VALUE
                changed = True
                tagged += 1
        elif REQUESTED_TAG in extra:
            extra.pop(REQUESTED_TAG)
            changed = True
            untagged += 1
        if changed:
            asset.metadata_extra = extra

    await db.flush()
    logger.info(
        "Requested refresh: %d songs requested (%d newly tagged, %d untagged, %d own categories restored)",
        len(popular_ids), tagged, untagged, healed,
    )
    return len(popular_ids)
