"""Audio conversion service — converts uploaded audio/video files using FFmpeg.

Uses subprocess pipes (no temp files) to convert any supported format and
extract duration metadata via ffprobe. Falls back to temp files for formats
like MPEG-PS that require seeking (can't use stdin pipes for those).
"""

import json
import logging
import os
import subprocess
import tempfile

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

# Formats that require seeking — pipe input won't work for these
SEEKABLE_EXTENSIONS = {".mpg", ".mpeg", ".avi", ".mkv", ".mov", ".mp4", ".webm"}


def _get_extension(filename: str) -> str:
    """Extract lowercase extension from filename."""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ""


def _extract_duration_tempfile(file_data: bytes, input_ext: str = ".bin") -> float | None:
    """Extract duration using a temp file — for formats that require seeking (e.g. MPEG-PS)."""
    if not input_ext.startswith("."):
        input_ext = "." + input_ext
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as f:
            f.write(file_data)
            tmp_path = f.name
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", tmp_path],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("ffprobe tempfile failed (rc=%d): %s", result.returncode, result.stderr[:300])
            return None
        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration")
        return float(duration_str) if duration_str else None
    except FileNotFoundError:
        logger.warning("ffprobe not found on system PATH")
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Duration extraction (tempfile) failed: %s", exc)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _extract_duration(file_data: bytes, input_ext: str = "") -> float | None:
    """Extract duration in seconds from audio data using ffprobe.

    Tries stdin pipe first; falls back to a temp file for seekable formats.
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
            logger.warning("ffprobe pipe failed (rc=%d) — trying temp file", result.returncode)
            if input_ext:
                return _extract_duration_tempfile(file_data, input_ext)
            return None

        data = json.loads(result.stdout)
        duration_str = data.get("format", {}).get("duration")
        if duration_str:
            return float(duration_str)
        # ffprobe succeeded but returned no duration — try temp file
        if input_ext:
            return _extract_duration_tempfile(file_data, input_ext)
        return None
    except FileNotFoundError:
        logger.warning("ffprobe not found on system PATH — cannot extract duration")
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Duration extraction failed: %s", exc)
        if input_ext:
            return _extract_duration_tempfile(file_data, input_ext)
        return None


CONVERT_FORMATS = {
    "mp3": {"ffmpeg_fmt": "mp3", "mime": "audio/mpeg", "ext": ".mp3", "args": ["-ab", "192k", "-ac", "2", "-ar", "44100"]},
    "wav": {"ffmpeg_fmt": "wav", "mime": "audio/wav", "ext": ".wav", "args": ["-ac", "2", "-ar", "44100"]},
    "flac": {"ffmpeg_fmt": "flac", "mime": "audio/flac", "ext": ".flac", "args": ["-ac", "2", "-ar", "44100"]},
    "ogg": {"ffmpeg_fmt": "ogg", "mime": "audio/ogg", "ext": ".ogg", "args": ["-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5"]},
    "aac": {"ffmpeg_fmt": "adts", "mime": "audio/aac", "ext": ".aac", "args": ["-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "192k"]},
}


def _convert_with_ffmpeg_tempfile(file_data: bytes, target_format: str, input_ext: str = ".bin") -> bytes | None:
    """Convert using temp files — more reliable for seekable formats like MPEG-PS."""
    fmt_config = CONVERT_FORMATS.get(target_format, CONVERT_FORMATS["mp3"])
    if not input_ext.startswith("."):
        input_ext = "." + input_ext
    in_path = None
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as in_f:
            in_f.write(file_data)
            in_path = in_f.name
        out_path = in_path + fmt_config["ext"]
        result = subprocess.run(
            [settings.FFMPEG_PATH, "-i", in_path, "-f", fmt_config["ffmpeg_fmt"]]
            + fmt_config["args"]
            + ["-v", "warning", "-y", out_path],
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning(
                "FFmpeg tempfile conversion failed (rc=%d): %s",
                result.returncode,
                result.stderr[:500].decode(errors="replace"),
            )
            return None
        with open(out_path, "rb") as out_f:
            data = out_f.read()
        return data if data else None
    except FileNotFoundError:
        logger.warning("FFmpeg not found at '%s'", settings.FFMPEG_PATH)
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("FFmpeg tempfile conversion error: %s", exc)
        return None
    finally:
        for path in (in_path, out_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _convert_with_ffmpeg(file_data: bytes, target_format: str = "mp3", input_ext: str = "") -> bytes | None:
    """Convert audio/video data to the target format using FFmpeg.

    Tries stdin/stdout pipes first; falls back to temp files for seekable
    formats like MPEG-PS (.mpg / .mpeg) that don't support pipe input.

    Returns converted bytes on success, or None if FFmpeg fails.
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
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning(
                "FFmpeg pipe conversion failed (rc=%d) — trying temp file: %s",
                result.returncode,
                result.stderr[:300].decode(errors="replace"),
            )
            if input_ext:
                return _convert_with_ffmpeg_tempfile(file_data, target_format, input_ext)
            return None

        out_data = result.stdout
        if len(out_data) == 0:
            logger.warning("FFmpeg produced empty output — trying temp file")
            if input_ext:
                return _convert_with_ffmpeg_tempfile(file_data, target_format, input_ext)
            return None

        return out_data
    except FileNotFoundError:
        logger.warning("FFmpeg not found at '%s' — storing original file as-is", settings.FFMPEG_PATH)
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("FFmpeg conversion error: %s", exc)
        if input_ext:
            return _convert_with_ffmpeg_tempfile(file_data, target_format, input_ext)
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
        duration = _extract_duration(file_data, ext)
        return file_data, duration, ext or ".bin"

    # Already in the target format — just extract duration
    fmt_config = CONVERT_FORMATS.get(target_format, CONVERT_FORMATS["mp3"])
    if ext == fmt_config["ext"]:
        logger.info("File '%s' is already %s — skipping conversion", original_filename, target_format)
        duration = _extract_duration(file_data, ext)
        return file_data, duration, ext

    # Attempt conversion (pipe first, temp-file fallback inside)
    logger.info("Converting '%s' (%s) to %s...", original_filename, ext, target_format)
    converted = _convert_with_ffmpeg(file_data, target_format, ext)

    if converted is not None:
        logger.info(
            "Conversion successful: %s -> %s (%.1f KB -> %.1f KB)",
            original_filename,
            target_format,
            len(file_data) / 1024,
            len(converted) / 1024,
        )
        duration = _extract_duration(converted, fmt_config["ext"])
        return converted, duration, fmt_config["ext"]

    # Conversion failed — fall back to original file
    logger.warning("Conversion failed for '%s' — storing original file as-is", original_filename)
    duration = _extract_duration(file_data, ext)
    return file_data, duration, ext or ".bin"


# Backwards-compatible alias
def convert_to_mp3(file_data: bytes, original_filename: str) -> tuple[bytes, float | None]:
    data, duration, _ext = convert_audio(file_data, original_filename, "mp3")
    return data, duration
