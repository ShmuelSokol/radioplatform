import asyncio
import os
import shutil

from app.config import settings

HLS_OUTPUT_DIR = "/tmp/hls"


async def generate_hls_segments(
    input_path: str,
    station_id: str,
    channel_name: str = "main",
    segment_duration: int = 6,
) -> str:
    """Generate HLS segments from an audio file.

    Returns the path to the m3u8 manifest.
    """
    output_dir = os.path.join(HLS_OUTPUT_DIR, station_id, channel_name)
    os.makedirs(output_dir, exist_ok=True)

    manifest_path = os.path.join(output_dir, "live.m3u8")
    segment_pattern = os.path.join(output_dir, "segment_%05d.ts")

    cmd = [
        settings.FFMPEG_PATH,
        "-re",
        "-i", input_path,
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "hls",
        "-hls_time", str(segment_duration),
        "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", segment_pattern,
        "-y",
        manifest_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Don't await — this runs as a live process
    return manifest_path, proc


async def stop_hls_process(proc: asyncio.subprocess.Process) -> None:
    """Gracefully stop an HLS encoding process."""
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()


def cleanup_hls_dir(station_id: str, channel_name: str = "main") -> None:
    """Remove HLS segments for a station channel."""
    output_dir = os.path.join(HLS_OUTPUT_DIR, station_id, channel_name)
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
