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
            if unplayable_reason(exc):
                try:
                    await check_and_take_off_air(asset_id, unplayable_reason(exc))
                except Exception as exc2:
                    logger.warning("Checking item %s for a broken file failed: %s", asset_id, exc2)


def unplayable_reason(exc: Exception) -> str | None:
    """A failure that says the file itself cannot be played (not a network hiccup)."""
    text = str(exc)
    if "Could not measure the length of the audio" in text:
        return "its length cannot be measured (the file is empty, broken or not audio)"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (400, 404):
        return f"its file is missing from storage (HTTP {status})"
    return None


async def check_and_take_off_air(asset_id: str, reason: str) -> bool:
    """Measure the whole file once more; if that fails too, take the item off the air.

    The quick analysis reads only a few ranges of the file, so a whole-file measure
    settles it. A broken item goes to do_not_play (no automatic pick uses it; the
    category it had is kept, so "allow again" puts it back), its waiting queue copies
    go, metadata_extra["unplayable"] says why, and an alert asks a person to look.
    Nothing is deleted. (2026-11-18: two items failed this way again and again in the
    upcoming-item analysis.) Returns True when the item was taken off the air."""
    import uuid
    from datetime import datetime, timezone

    from app.db.session import async_session_factory
    from app.services.alert_service import create_alert
    from app.services.asset_service import get_asset, set_do_not_play
    from app.services.audio_analysis_service import analyze_audio

    async with async_session_factory() as db:
        try:
            await analyze_audio(db, asset_id, full=True)
            await db.commit()
            return False  # the whole file measures: the quick read was the problem
        except Exception as exc:
            if not unplayable_reason(exc):
                raise  # a network error now says nothing about the file
            await db.rollback()
        aid = uuid.UUID(asset_id)
        result = await set_do_not_play(db, aid, True)
        asset = await get_asset(db, aid)
        extra = dict(asset.metadata_extra or {})
        extra["unplayable"] = {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}
        asset.metadata_extra = extra
        await create_alert(
            db, "asset_missing", "warning",
            f"Taken off the air: {asset.title}",
            f'"{asset.title}" was taken off the air because {reason}. It was not deleted: '
            f"check the file in the Library, replace it, and allow it again with Song actions.",
            context={"asset_id": asset_id, "removed_from_queue": result.get("removed_from_queue")},
        )
        await db.commit()
        logger.warning("Item %s taken off the air: %s", asset_id, reason)
        return True


async def restore_measurable_items() -> int:
    """Put back on the air every item check_and_take_off_air took off whose file now
    measures fine. Returns how many came back.

    2026-11-18: the whole-file measure read the audio from a pipe, where FFmpeg gave
    up on two good stories ("chanuka radio pt02", 35 min; "Story the Riminover and the
    wind", 15 min), so they were taken off the air by mistake. Runs once after each
    start. Only items this check took off (metadata_extra["unplayable"]) are looked at;
    an item a person marked "do not play" is never touched. Each comes back to the
    category it had, its alert is resolved and the Activity log says so."""
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.alert import Alert
    from app.models.asset import Asset
    from app.services import activity_log
    from app.services.asset_service import DO_NOT_PLAY, get_asset, set_do_not_play
    from app.services.audio_analysis_service import analyze_audio

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Asset.id, Asset.metadata_extra).where(Asset.category == DO_NOT_PLAY)
        )).all()
    candidates = [str(aid) for aid, extra in rows if isinstance(extra, dict) and extra.get("unplayable")]
    restored = 0
    for asset_id in candidates:
        try:
            async with async_session_factory() as db:
                analysis = await analyze_audio(db, asset_id, full=True)
                await db.commit()
                asset = await get_asset(db, uuid.UUID(asset_id))
                extra = dict(asset.metadata_extra or {})
                was = extra.pop("unplayable", None)
                extra["restored_after_unplayable"] = {
                    "was": was, "at": datetime.now(timezone.utc).isoformat(),
                    "length": analysis.get("duration"),
                }
                asset.metadata_extra = extra
                await db.flush()
                result = await set_do_not_play(db, asset.id, False)
                title = asset.title
                alerts = (await db.execute(
                    select(Alert).where(Alert.title == f"Taken off the air: {title}", Alert.is_resolved == False)  # noqa: E712
                )).scalars().all()
                for alert in alerts:
                    alert.is_resolved = True
                    alert.resolved_at = datetime.now(timezone.utc)
                    ctx = dict(alert.context or {})
                    ctx["cleared_by_itself"] = "the file measures fine; the item is back on the air"
                    alert.context = ctx
                await db.commit()
        except Exception as exc:  # still broken, or storage unreachable: leave it off
            logger.info("Item %s stays off the air: %s", asset_id, exc)
            continue
        restored += 1
        logger.warning("Item %s (%s) back on the air: its file measures %.1f s", asset_id, title, analysis.get("duration") or 0)
        activity_log.record(
            None, user_id=None, user_email="KBR Live (automatic)", user_role="system",
            label="Put back on the air (library item)", category="library", action="update",
            resource_type="asset", resource_id=asset_id,
            detail=f'"{title}" is back in "{result.get("category")}": it was taken off the air by mistake '
                   f'(its length could not be read), and its file now measures fine.',
            changes={"category": {"before": DO_NOT_PLAY, "after": result.get("category")}},
        )
    return restored


async def restore_on_startup(delay_seconds: float = 90.0) -> None:
    """Shortly after a start, put back items wrongly taken off the air."""
    if not ENABLED:
        return
    await asyncio.sleep(delay_seconds)
    try:
        await restore_measurable_items()
    except Exception as exc:
        logger.warning("Putting items back on the air stopped: %s", exc)
