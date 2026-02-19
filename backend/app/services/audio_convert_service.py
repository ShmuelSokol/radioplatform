"""Audio conversion service — converts uploaded audio/video files to MP3 using FFmpeg.

Uses subprocess pipes (no temp files) to convert any supported format to MP3 and
extract duration metadata via ffprobe.
"""

import json
import logging
import subprocess

from app.config import settings

logger = logging.getLogger(__name__)

# Extensions that are already MP3 (skip conversion, still extract duration)
MP3_EXTENSIONS = {".mp3"}

# Extensions that should be converted to MP3
CONVERTIBLE_EXTENSIONS = {
    ".mpg", ".mpeg", ".wav", ".flac", ".ogg", ".m4a", ".wma",
    ".aac", ".opus", ".webm", ".mp4", ".avi", ".mkv", ".mov",
    ".wv", ".ape", ".alac", ".aiff", ".aif",
}


def _get_extension(filename: str) -> str:
    """Extract lowercase extension from filename."""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ""


def _extract_duration(file_data: bytes) -> float | None:
    """Extract duration in seconds from audio data using ffprobe via stdin pipe.

    Returns None if ffprobe fails or is not available.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-i", "pipe:0",
            ],
            input=file_data,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("ffprobe failed (rc=%d): %s", result.returncode, result.stderr[:500])
            return None

        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration")
        if duration_str:
            return float(duration_str)
        return None
    except FileNotFoundError:
        logger.warning("ffprobe not found on system PATH — cannot extract duration")
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Duration extraction failed: %s", exc)
        return None


def _convert_with_ffmpeg(file_data: bytes) -> bytes | None:
    """Convert audio/video data to MP3 using FFmpeg via stdin/stdout pipes.

    Returns MP3 bytes on success, or None if FFmpeg fails or is not available.
    """
    try:
        result = subprocess.run(
            [
                settings.FFMPEG_PATH,
                "-i", "pipe:0",
                "-f", "mp3",
                "-ab", "192k",
                "-ac", "2",
                "-ar", "44100",
                "-v", "warning",
                "pipe:1",
            ],
            input=file_data,
            capture_output=True,
            timeout=300,  # 5 min max for large files
        )
        if result.returncode != 0:
            logger.warning(
                "FFmpeg conversion failed (rc=%d): %s",
                result.returncode,
                result.stderr[:1000].decode(errors="replace"),
            )
            return None

        mp3_data = result.stdout
        if len(mp3_data) == 0:
            logger.warning("FFmpeg produced empty output")
            return None

        return mp3_data
    except FileNotFoundError:
        logger.warning("FFmpeg not found at '%s' — storing original file as-is", settings.FFMPEG_PATH)
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("FFmpeg conversion error: %s", exc)
        return None


def convert_to_mp3(file_data: bytes, original_filename: str) -> tuple[bytes, float | None]:
    """Convert an audio/video file to MP3 format and extract its duration.

    Args:
        file_data: Raw bytes of the uploaded file.
        original_filename: Original filename (used to detect format).

    Returns:
        A tuple of (file_bytes, duration_seconds). If conversion fails or is
        not needed, the original bytes are returned. Duration may be None if
        extraction fails.
    """
    ext = _get_extension(original_filename)

    # Already MP3 — just extract duration, no conversion needed
    if ext in MP3_EXTENSIONS:
        logger.info("File '%s' is already MP3 — skipping conversion", original_filename)
        duration = _extract_duration(file_data)
        return file_data, duration

    # Not a recognized audio/video extension — still try to extract duration but skip conversion
    if ext not in CONVERTIBLE_EXTENSIONS:
        logger.info(
            "File '%s' has unrecognized extension '%s' — attempting conversion anyway",
            original_filename, ext,
        )

    # Attempt conversion
    logger.info("Converting '%s' (%s) to MP3...", original_filename, ext)
    mp3_data = _convert_with_ffmpeg(file_data)

    if mp3_data is not None:
        logger.info(
            "Conversion successful: %s -> MP3 (%.1f KB -> %.1f KB)",
            original_filename,
            len(file_data) / 1024,
            len(mp3_data) / 1024,
        )
        # Extract duration from the converted MP3
        duration = _extract_duration(mp3_data)
        return mp3_data, duration

    # Conversion failed — fall back to original file
    logger.warning("Conversion failed for '%s' — storing original file as-is", original_filename)
    duration = _extract_duration(file_data)
    return file_data, duration
