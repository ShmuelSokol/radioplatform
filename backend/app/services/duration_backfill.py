"""Fill in missing item lengths.

An item with no duration broke the control room's queue read on KBR Jerusalem
(`duration - elapsed` with duration NULL) and plays for a guessed 3 minutes.
This measures the real length from the audio file.
"""
import asyncio
import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset

logger = logging.getLogger(__name__)


async def _read_audio(file_path: str) -> bytes:
    if file_path.startswith(("http://", "https://")):
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(file_path, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
    from app.services.storage_service import download_file
    return await download_file(file_path)


async def measure(file_path: str) -> float:
    from app.services.silence_service import get_audio_duration
    data = await _read_audio(file_path)
    return await asyncio.to_thread(get_audio_duration, data)


async def backfill_missing_durations(db: AsyncSession, limit: int = 100) -> dict:
    """Measure up to `limit` items that have a file but no length. Returns counts."""
    missing = or_(Asset.duration.is_(None), Asset.duration <= 0)
    rows = (await db.execute(
        select(Asset).where(missing, Asset.file_path.isnot(None), Asset.file_path != "").limit(limit)
    )).scalars().all()
    fixed, failed = 0, []
    for asset in rows:
        try:
            seconds = await measure(asset.file_path)
        except Exception as exc:
            logger.warning("Could not measure '%s' (%s): %s", asset.title, asset.id, exc)
            seconds = 0.0
        if seconds > 0:
            asset.duration = seconds
            fixed += 1
            await db.commit()
        else:
            failed.append(str(asset.id))
    remaining = (await db.execute(
        select(Asset.id).where(missing, Asset.file_path.isnot(None), Asset.file_path != "")
    )).all()
    if rows:
        logger.info("Item lengths filled in: %d, could not measure: %d, still missing: %d",
                    fixed, len(failed), len(remaining))
    return {"fixed": fixed, "failed": failed, "still_missing": len(remaining)}


async def backfill_on_startup(delay_seconds: float = 60.0) -> None:
    """Shortly after a start, fill in what is missing (in batches, off the request path)."""
    from app.db.engine import async_session_factory
    await asyncio.sleep(delay_seconds)
    try:
        for _ in range(20):  # at most 2,000 items per start
            async with async_session_factory() as db:
                result = await backfill_missing_durations(db, limit=100)
            if result["fixed"] == 0:
                break
    except Exception as exc:
        logger.warning("Item length backfill stopped: %s", exc)
