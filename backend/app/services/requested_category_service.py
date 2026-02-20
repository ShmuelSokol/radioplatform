"""Dynamic "requested" category service.

Songs with N+ requests in the last 30 days are auto-tagged with
category="requested". When they drop below the threshold, the
original category is restored.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.song_request import SongRequest

logger = logging.getLogger(__name__)


async def refresh_requested_category(db: AsyncSession, threshold: int = 3) -> int:
    """Refresh the "requested" dynamic category.

    Returns the number of assets currently in the "requested" category.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # Find assets with >= threshold requests in the last 30 days
    popular_q = (
        select(SongRequest.asset_id, func.count(SongRequest.id).label("cnt"))
        .where(
            SongRequest.asset_id.isnot(None),
            SongRequest.created_at >= cutoff,
        )
        .group_by(SongRequest.asset_id)
        .having(func.count(SongRequest.id) >= threshold)
    )
    result = await db.execute(popular_q)
    popular_ids = {row[0] for row in result.all()}

    # Tag popular assets as "requested" (save original category in metadata_extra)
    if popular_ids:
        assets_result = await db.execute(
            select(Asset).where(Asset.id.in_(popular_ids))
        )
        for asset in assets_result.scalars().all():
            if asset.category != "requested":
                extra = dict(asset.metadata_extra or {})
                extra["original_category"] = asset.category
                asset.metadata_extra = extra
                asset.category = "requested"

    # Restore assets that were "requested" but no longer meet threshold
    stale_result = await db.execute(
        select(Asset).where(
            Asset.category == "requested",
            Asset.id.notin_(popular_ids) if popular_ids else Asset.category == "requested",
        )
    )
    restored = 0
    for asset in stale_result.scalars().all():
        if asset.id not in popular_ids:
            extra = dict(asset.metadata_extra or {})
            original = extra.pop("original_category", None)
            asset.category = original
            asset.metadata_extra = extra
            restored += 1

    await db.flush()
    logger.info(
        "Requested category refresh: %d assets tagged, %d restored",
        len(popular_ids), restored,
    )
    return len(popular_ids)
