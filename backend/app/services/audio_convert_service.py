"""Audio conversion service — converts uploaded audio/video files using FFmpeg.

Uses subprocess pipes (no temp files) to convert any supported format and
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


CONVERT_FORMATS = {
    "mp3": {"ffmpeg_fmt": "mp3", "mime": "audio/mpeg", "ext": ".mp3", "args": ["-ab", "192k", "-ac", "2", "-ar", "44100"]},
    "wav": {"ffmpeg_fmt": "wav", "mime": "audio/wav", "ext": ".wav", "args": ["-ac", "2", "-ar", "44100"]},
    "flac": {"ffmpeg_fmt": "flac", "mime": "audio/flac", "ext": ".flac", "args": ["-ac", "2", "-ar", "44100"]},
    "ogg": {"ffmpeg_fmt": "ogg", "mime": "audio/ogg", "ext": ".ogg", "args": ["-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5"]},
    "aac": {"ffmpeg_fmt": "adts", "mime": "audio/aac", "ext": ".aac", "args": ["-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "192k"]},
}


def _convert_with_ffmpeg(file_data: bytes, target_format: str = "mp3") -> bytes | None:
    """Convert audio/video data to the target format using FFmpeg via stdin/stdout pipes.

    Returns converted bytes on success, or None if FFmpeg fails or is not available.
    """
    fmt_config = CONVERT_FORMATS.get(target_format)
    if not fmt_config:
        logger.warning("Unknown target format '%s' — falling back to mp3", target_format)
        fmt_config = CONVERT_FORMATS["mp3"]

    try:
        result = subprocess.run(
            [
                settings.FFMPEG_PATH,
                "-i", "pipe:0",
                "-f", fmt_config["ffmpeg_fmt"],
            ] + fmt_config["args"] + [
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

        out_data = result.stdout
        if len(out_data) == 0:
            logger.warning("FFmpeg produced empty output")
            return None

        return out_data
    except FileNotFoundError:
        logger.warning("FFmpeg not found at '%s' — storing original file as-is", settings.FFMPEG_PATH)
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("FFmpeg conversion error: %s", exc)
        return None


def convert_audio(
    file_data: bytes,
    original_filename: str,
    target_format: str = "mp3",
) -> tuple[bytes, float | None, str]:
    """Convert an audio/video file to the requested format and extract its duration.

    Args:
        file_data: Raw bytes of the uploaded file.
        original_filename: Original filename (used to detect format).
        target_format: Target format (mp3, wav, flac, ogg, aac, or original).

    Returns:
        A tuple of (file_bytes, duration_seconds, file_extension).
        If conversion fails, the original bytes are returned.
        Duration may be None if extraction fails.
    """
    ext = _get_extension(original_filename)

    # "original" means no conversion
    if target_format == "original":
        logger.info("Keeping original format for '%s'", original_filename)
        duration = _extract_duration(file_data)
        return file_data, duration, ext or ".bin"

    # Already in the target format — just extract duration
    fmt_config = CONVERT_FORMATS.get(target_format, CONVERT_FORMATS["mp3"])
    if ext == fmt_config["ext"]:
        logger.info("File '%s' is already %s — skipping conversion", original_filename, target_format)
        duration = _extract_duration(file_data)
        return file_data, duration, ext

    # Attempt conversion
    logger.info("Converting '%s' (%s) to %s...", original_filename, ext, target_format)
    converted = _convert_with_ffmpeg(file_data, target_format)

    if converted is not None:
        logger.info(
            "Conversion successful: %s -> %s (%.1f KB -> %.1f KB)",
            original_filename,
            target_format,
            len(file_data) / 1024,
            len(converted) / 1024,
        )
        duration = _extract_duration(converted)
        return converted, duration, fmt_config["ext"]

    # Conversion failed — fall back to original file
    logger.warning("Conversion failed for '%s' — storing original file as-is", original_filename)
    duration = _extract_duration(file_data)
    return file_data, duration, ext or ".bin"


# Backwards-compatible alias
def convert_to_mp3(file_data: bytes, original_filename: str) -> tuple[bytes, float | None]:
    data, duration, _ext = convert_audio(file_data, original_filename, "mp3")
    return data, duration
