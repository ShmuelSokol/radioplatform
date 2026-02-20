"""Audio mixer service — combines two audio tracks using FFmpeg filter_complex.

Uses temp files for I/O (matches audio_convert_service.py pattern).
"""

import logging
import os
import subprocess
import tempfile

from app.config import settings

logger = logging.getLogger(__name__)

LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"


def mix_audio(
    backtrack_data: bytes,
    overlay_data: bytes,
    bt_ext: str = ".mp3",
    ov_ext: str = ".mp3",
    bt_trim_start: float = 0.0,
    bt_trim_end: float = 0.0,
    bt_target_dur: float = 0.0,
    bt_volume: float = 0.2,
    ov_volume: float = 1.0,
    bt_fade_in: float = 0.0,
    bt_fade_out: float = 2.0,
    bt_fade_out_start: float = 0.0,
    ov_fade_in: float = 0.0,
    ov_fade_out: float = 0.0,
    ov_fade_out_start: float = 0.0,
) -> tuple[bytes, float | None]:
    """Mix backtrack + overlay into a single MP3 file.

    Returns (mixed_bytes, duration_seconds).
    """
    bt_path = None
    ov_path = None
    out_path = None

    try:
        # Write temp input files
        with tempfile.NamedTemporaryFile(suffix=bt_ext, delete=False) as f:
            f.write(backtrack_data)
            bt_path = f.name
        with tempfile.NamedTemporaryFile(suffix=ov_ext, delete=False) as f:
            f.write(overlay_data)
            ov_path = f.name
        out_path = bt_path + "_mixed.mp3"

        # Build backtrack filter chain
        bt_filters: list[str] = []
        if bt_trim_end > 0:
            bt_filters.append(f"atrim=start={bt_trim_start}:end={bt_trim_end}")
            bt_filters.append("asetpts=PTS-STARTPTS")
        if bt_target_dur > 0:
            bt_filters.append(f"apad=whole_dur={bt_target_dur}")
        bt_filters.append(f"volume={bt_volume}")
        if bt_fade_in > 0:
            bt_filters.append(f"afade=t=in:st=0:d={bt_fade_in}")
        if bt_fade_out > 0 and bt_fade_out_start > 0:
            bt_filters.append(f"afade=t=out:st={bt_fade_out_start}:d={bt_fade_out}")
        bt_chain = ",".join(bt_filters)

        # Build overlay filter chain
        ov_filters: list[str] = []
        ov_filters.append(f"volume={ov_volume}")
        if ov_fade_in > 0:
            ov_filters.append(f"afade=t=in:st=0:d={ov_fade_in}")
        if ov_fade_out > 0 and ov_fade_out_start > 0:
            ov_filters.append(f"afade=t=out:st={ov_fade_out_start}:d={ov_fade_out}")
        ov_chain = ",".join(ov_filters)

        # Full filter_complex
        filter_complex = (
            f"[0:a]{bt_chain}[bg];"
            f"[1:a]{ov_chain}[fg];"
            f"[bg][fg]amix=inputs=2:duration=longest:normalize=0,"
            f"{LOUDNORM_FILTER}[out]"
        )

        cmd = [
            settings.FFMPEG_PATH,
            "-i", bt_path,
            "-i", ov_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ac", "2",
            "-ar", "44100",
            "-b:a", "256k",
            "-f", "mp3",
            "-v", "warning",
            "-y", out_path,
        ]

        logger.info("Running mixer FFmpeg: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, timeout=300)

        if result.returncode != 0:
            stderr = result.stderr[:1000].decode(errors="replace")
            logger.error("Mixer FFmpeg failed (rc=%d): %s", result.returncode, stderr)
            raise RuntimeError(f"FFmpeg mix failed: {stderr}")

        with open(out_path, "rb") as f:
            mixed_data = f.read()

        if not mixed_data:
            raise RuntimeError("FFmpeg produced empty output")

        # Extract duration from output
        duration = _extract_duration(out_path)

        logger.info("Mix complete: %d bytes, duration=%s", len(mixed_data), duration)
        return mixed_data, duration

    finally:
        for path in (bt_path, ov_path, out_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _extract_duration(file_path: str) -> float | None:
    """Extract duration from a file using ffprobe."""
    import json

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration")
        return float(duration_str) if duration_str else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
        return None
