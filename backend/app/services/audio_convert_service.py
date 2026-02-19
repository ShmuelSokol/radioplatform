"""Audio conversion service — converts uploaded audio/video files using FFmpeg.

Always uses temp files for conversion to ensure reliable output across all
formats. Pipe mode (stdin/stdout) is unreliable for many container formats
and can produce garbage audio that "succeeds" with rc=0.
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile

from app.config import settings

logger = logging.getLogger(__name__)

# Extensions that are already MP3 (skip conversion, still extract duration)
MP3_EXTENSIONS = {".mp3"}

# Extensions that should be converted
CONVERTIBLE_EXTENSIONS = {
    ".mp2", ".mpg", ".mpeg", ".wav", ".flac", ".ogg", ".m4a", ".wma",
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

    Always uses temp files for reliable format detection.
    Returns None if ffprobe fails or is not available.
    """
    ext = input_ext if input_ext else ".bin"
    return _extract_duration_tempfile(file_data, ext)


# EBU R128 loudness normalization filter — broadcast standard
# -16 LUFS integrated loudness (internet/streaming standard)
# -1.5 dBTP true peak limit (headroom for lossy codec inter-sample peaks)
# 11 LU loudness range (preserves dynamics while ensuring consistency)
LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"

CONVERT_FORMATS = {
    "mp2": {"ffmpeg_fmt": "mp2", "mime": "audio/mpeg", "ext": ".mp2", "args": ["-vn", "-af", LOUDNORM_FILTER, "-c:a", "mp2", "-b:a", "192k", "-ac", "2", "-ar", "44100"]},
    "mp3": {"ffmpeg_fmt": "mp3", "mime": "audio/mpeg", "ext": ".mp3", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ab", "192k", "-ac", "2", "-ar", "44100"]},
    "mp4": {"ffmpeg_fmt": "mp4", "mime": "audio/mp4", "ext": ".m4a", "args": ["-vn", "-af", LOUDNORM_FILTER, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100", "-movflags", "+faststart"]},
    "wav": {"ffmpeg_fmt": "wav", "mime": "audio/wav", "ext": ".wav", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100"]},
    "flac": {"ffmpeg_fmt": "flac", "mime": "audio/flac", "ext": ".flac", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100"]},
    "ogg": {"ffmpeg_fmt": "ogg", "mime": "audio/ogg", "ext": ".ogg", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5"]},
    "aac": {"ffmpeg_fmt": "adts", "mime": "audio/aac", "ext": ".aac", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "192k"]},
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

    Always uses temp files — pipe mode (stdin/stdout) is unreliable for many
    container formats and can produce garbage audio even with rc=0.

    Returns converted bytes on success, or None if FFmpeg fails.
    """
    ext = input_ext if input_ext else ".bin"
    return _convert_with_ffmpeg_tempfile(file_data, target_format, ext)


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

    # Diagnostic: log input file fingerprint
    input_hash = hashlib.md5(file_data[:4096]).hexdigest()
    logger.info(
        "convert_audio START: file='%s', ext='%s', target='%s', size=%d, hash_4k=%s",
        original_filename, ext, target_format, len(file_data), input_hash,
    )

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
        output_hash = hashlib.md5(converted[:4096]).hexdigest()
        logger.info(
            "Conversion successful: %s -> %s (%.1f KB -> %.1f KB, out_hash_4k=%s)",
            original_filename,
            target_format,
            len(file_data) / 1024,
            len(converted) / 1024,
            output_hash,
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
