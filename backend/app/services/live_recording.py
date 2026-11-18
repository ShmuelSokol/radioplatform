"""Every live show is recorded as it aired and lands in Archives as a draft.

The recording taps the output of the show's broadcast delay (`LiveAir.tap`), so it is
exactly what listeners heard: after the delay, without anything that was dumped, with
the callers and the time checks. Speech-quality MP3 (mono, 32 kbps: about 14 MB an
hour, so a three-hour show stays under the storage's 50 MB file limit). When the show
is over for listeners the file is stored and a ShowArchive row is made, unpublished: a
manager listens and publishes it on the Archives page.

Station setting `automation_config.live_record_shows` (default on). A host who drops
and comes back after the delay had played out starts a second part.
See wiki/live-shows.md, "Recording".
"""
import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.services.live_air import RATE

logger = logging.getLogger(__name__)

MIN_SECONDS = 20.0          # shorter than this is a test or a slip, not a show
BITRATE = "32k"
_running: set = set()       # recorder tasks (kept from collection)


def wanted(config: dict | None) -> bool:
    return (config or {}).get("live_record_shows", True) is not False


def start(station_id: str, show_id: str, air) -> None:
    """Record this show's air until it closes. Called when the delay line is created."""
    import shutil

    if shutil.which("ffmpeg") is None:
        logger.warning("Live show %s is not recorded: ffmpeg is not installed", show_id)
        return
    task = asyncio.ensure_future(_record(station_id, show_id, air))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def capture(air, path: str) -> tuple[float, datetime | None]:
    """Write what listeners hear to an MP3 file until the air closes.
    Returns (seconds recorded, when the first sound aired)."""
    tap = air.tap()
    proc = None
    samples = 0
    started = None
    try:
        while True:
            data = await tap.get()
            if data is None:
                break
            if proc is None:
                started = datetime.now(timezone.utc)
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
                    "-codec:a", "libmp3lame", "-b:a", BITRATE, "-ac", "1", path,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            proc.stdin.write(data)
            await proc.stdin.drain()
            samples += len(data) // 2
    finally:
        air.untap(tap)
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 30)
            except Exception:
                proc.kill()
    return samples / RATE, started


async def save(db, station_id, show_id, path: str, seconds: float, started: datetime | None):
    """Store the file and make the (unpublished) archive entry. Returns it, or None."""
    from sqlalchemy import func, select

    from app.models.live_show import LiveShow
    from app.models.show_archive import ShowArchive
    from app.models.user import User
    from app.services.storage_service import upload_file

    show = await db.get(LiveShow, uuid.UUID(str(show_id)))
    if show is None:
        return None
    with open(path, "rb") as f:
        data = f.read()
    key = f"show-recordings/{show_id}/{uuid.uuid4()}.mp3"
    await upload_file(data, key, "audio/mpeg")
    if settings.supabase_storage_enabled:
        url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}/{key}"
    else:
        url = key
    parts = (await db.execute(
        select(func.count()).select_from(ShowArchive).where(ShowArchive.live_show_id == show.id)
    )).scalar() or 0
    host = await db.get(User, show.host_user_id) if show.host_user_id else None
    archive = ShowArchive(
        id=uuid.uuid4(), station_id=show.station_id,
        title=show.title if parts == 0 else f"{show.title} (part {parts + 1})",
        description=show.description,
        host_name=(getattr(host, "display_name", None) or getattr(host, "email", None)) if host else None,
        recorded_at=started or datetime.now(timezone.utc), duration_seconds=int(round(seconds)),
        audio_url=url, is_published=False, live_show_id=show.id,
    )
    db.add(archive)
    await db.flush()
    return archive


async def _record(station_id: str, show_id: str, air) -> None:
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="liveshow-")
    os.close(fd)
    try:
        seconds, started = await capture(air, path)
        if seconds < MIN_SECONDS:
            logger.info("Live show %s: %.0f s aired, too short to keep a recording", show_id, seconds)
            return
        from app.db.session import get_async_session

        async for db in get_async_session():
            try:
                archive = await save(db, station_id, show_id, path, seconds, started)
                await db.commit()
                if archive is not None:
                    logger.info("Live show %s recorded: %.0f s, archive %s (draft)", show_id, seconds, archive.id)
            except Exception:
                await db.rollback()
                raise
            break
    except Exception:
        logger.exception("Recording of live show %s failed", show_id)
        try:
            from app.db.session import get_async_session
            from app.services.alert_service import create_alert

            async for db in get_async_session():
                await create_alert(
                    db, alert_type="live_show", severity="warning", title="Live show recording was not saved",
                    message="The recording of a live show could not be stored, so it is not in Archives.",
                    station_id=station_id,
                )
                await db.commit()
                break
        except Exception:
            logger.warning("Could not raise the recording alert", exc_info=True)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
