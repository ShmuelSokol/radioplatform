"""Analyse items shortly before they air.

Most of the library was imported without an audio analysis, and analyses made
before ANALYSIS_VERSION hold wrong end and crossfade points (every item measured
as 180 s). Without good points the player crossfades 3 s before the stored end,
which on a song with a long quiet tail is 2 to 3 s of near silence. So when an
item is on air or next, and its analysis is missing or old, it is analysed in the
background, one at a time, well before the hand-over.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

# Switched off in tests (no audio to download there)
ENABLED = True

_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
_seen: set[str] = set()  # asked for in this process: analysed, queued or failed


def request(asset) -> None:
    """Queue `asset` for analysis if it needs one. Cheap; safe to call every tick."""
    global _queue, _worker
    from app.models.asset import needs_analysis

    if not ENABLED or asset is None or not getattr(asset, "file_path", None):
        return
    key = str(asset.id)
    if key in _seen or not needs_analysis(asset):
        return
    _seen.add(key)
    if _queue is None:
        _queue = asyncio.Queue()
    _queue.put_nowait(key)
    if _worker is None or _worker.done():
        _worker = asyncio.get_running_loop().create_task(_run())


async def _run() -> None:
    from app.db.session import async_session_factory
    from app.services.audio_analysis_service import analyze_audio

    while _queue is not None and not _queue.empty():
        asset_id = await _queue.get()
        try:
            async with async_session_factory() as db:
                await analyze_audio(db, asset_id)
                await db.commit()
        except Exception as exc:  # one bad file must not stop the others
            logger.warning("Analysis of upcoming item %s failed: %s", asset_id, exc)
