"""Silence detection and audio trimming via FFmpeg."""
import logging
import re
import subprocess
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def get_audio_duration(file_data: bytes) -> float:
    """Length of the audio in seconds, from decoding it to the end.

    The file arrives on a pipe, where ffmpeg usually cannot read a length from the
    header ("Duration: N/A" for MP3), so the decoder's last "time=" is the length.
    Before this, every analysed item got the 180 s fallback as its length.
    """
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=120)
    stderr = result.stderr.decode("utf-8", errors="replace")
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if times:
        h, m, s = times[-1]
        decoded = int(h) * 3600 + int(m) * 60 + float(s)
        if decoded > 0:
            return decoded
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", stderr)
    if match:
        h, m, s, cs = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        return h * 3600 + m * 60 + s + cs / 100.0
    return 0.0


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
    """Trim audio to [trim_start, trim_end] into an MP3.

    An MP3 is cut by stream copy (no quality loss). Anything else (WAV, FLAC,
    MP2...) cannot be copied into an MP3 file, so it is encoded.
    Returns (trimmed_bytes, new_duration).
    """
    duration = trim_end - trim_start

    def run(codec_args: list[str]) -> subprocess.CompletedProcess:
        cmd = [
            settings.FFMPEG_PATH, "-v", "error", "-i", "pipe:0",
            "-ss", str(trim_start),
            "-to", str(trim_end),
            "-vn", *codec_args,
            "-f", "mp3",
            "pipe:1",
        ]
        return subprocess.run(cmd, input=file_data, capture_output=True, timeout=120)

    from app.services.audio_convert_service import probe_codec

    result = run(["-c:a", "copy"]) if probe_codec(file_data) == "mp3" else None
    if result is None or result.returncode != 0 or not result.stdout:
        result = run(["-c:a", "libmp3lame", "-b:a", "256k", "-ar", "44100"])
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"FFmpeg trim failed: {result.stderr[:500]}")

    return result.stdout, duration
