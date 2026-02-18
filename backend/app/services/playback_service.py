import asyncio
import os
import tempfile
import uuid

from app.services.storage_service import download_file
from app.streaming.hls_generator import generate_hls_segments, stop_hls_process, HLS_OUTPUT_DIR
from app.streaming.playlist_engine import PlaylistEngine

# In-memory process registry (per station)
_active_processes: dict[str, asyncio.subprocess.Process] = {}


async def start_playback(station_id: str) -> dict:
    """Start playing from the queue."""
    engine = PlaylistEngine(station_id)

    item = await engine.dequeue()
    if not item:
        return {"status": "error", "message": "Queue is empty"}

    # Download file from S3 to temp
    file_data = await download_file(item["file_path"])
    ext = item["file_path"].rsplit(".", 1)[-1] if "." in item["file_path"] else "mp3"
    tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
    tmp.write(file_data)
    tmp.close()

    # Stop any existing process
    await stop_current(station_id)

    # Generate HLS
    manifest_path, proc = await generate_hls_segments(tmp.name, station_id)
    _active_processes[station_id] = proc

    await engine.set_now_playing(item)
    await engine.set_state("playing")

    return {
        "status": "playing",
        "now_playing": item,
        "manifest": f"/hls/{station_id}/main/live.m3u8",
    }


async def stop_current(station_id: str) -> None:
    """Stop current playback."""
    proc = _active_processes.pop(station_id, None)
    if proc:
        await stop_hls_process(proc)

    engine = PlaylistEngine(station_id)
    await engine.clear_now_playing()
    await engine.set_state("stopped")


async def pause_playback(station_id: str) -> dict:
    engine = PlaylistEngine(station_id)
    await engine.set_state("paused")
    return {"status": "paused"}


async def play_now(station_id: str, asset_id: str, title: str, file_path: str, duration: float) -> dict:
    """Play a specific asset immediately, pushing current to front of queue."""
    engine = PlaylistEngine(station_id)

    # Enqueue the asset at front (we'll just start playback with it)
    await stop_current(station_id)

    file_data = await download_file(file_path)
    ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "mp3"
    tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
    tmp.write(file_data)
    tmp.close()

    manifest_path, proc = await generate_hls_segments(tmp.name, station_id)
    _active_processes[station_id] = proc

    item = {"asset_id": asset_id, "title": title, "file_path": file_path, "duration": duration}
    await engine.set_now_playing(item)
    await engine.set_state("playing")

    return {
        "status": "playing",
        "now_playing": item,
        "manifest": f"/hls/{station_id}/main/live.m3u8",
    }


async def get_now_playing(station_id: str) -> dict:
    engine = PlaylistEngine(station_id)
    now = await engine.get_now_playing()
    state = await engine.get_state()
    queue = await engine.peek_queue(5)
    return {
        "state": state,
        "now_playing": now,
        "upcoming": queue,
    }
