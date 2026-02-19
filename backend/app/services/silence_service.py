"""Silence detection and audio trimming via FFmpeg."""
import logging
import re
import subprocess
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def detect_silence(
    file_data: bytes,
    threshold_db: float = -30,
    min_duration: float = 0.5,
) -> list[dict[str, Any]]:
    """Run FFmpeg silencedetect filter and return silence regions.

    Returns a list of dicts: [{"start": float, "end": float, "duration": float}, ...]
    """
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=120)
    stderr = result.stderr.decode("utf-8", errors="replace")

    regions: list[dict[str, Any]] = []
    starts: list[float] = []

    for line in stderr.splitlines():
        start_match = re.search(r"silence_start:\s*([\d.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))

        end_match = re.search(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", line)
        if end_match and starts:
            s = starts.pop(0)
            regions.append({
                "start": s,
                "end": float(end_match.group(1)),
                "duration": float(end_match.group(2)),
            })

    return regions


def trim_audio(
    file_data: bytes,
    trim_start: float,
    trim_end: float,
) -> tuple[bytes, float]:
    """Trim audio to [trim_start, trim_end] using stream copy.

    Returns (trimmed_bytes, new_duration).
    """
    duration = trim_end - trim_start
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-ss", str(trim_start),
        "-to", str(trim_end),
        "-c", "copy",
        "-f", "mp3",
        "pipe:1",
    ]
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg trim failed: {result.stderr[:500]}")

    return result.stdout, duration
