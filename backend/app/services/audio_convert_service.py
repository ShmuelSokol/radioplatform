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
    "mp2": {"ffmpeg_fmt": "mp2", "mime": "audio/mpeg", "ext": ".mp2", "args": ["-vn", "-af", LOUDNORM_FILTER, "-c:a", "mp2", "-b:a", "256k", "-ac", "2", "-ar", "44100"]},
    "mp3": {"ffmpeg_fmt": "mp3", "mime": "audio/mpeg", "ext": ".mp3", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ab", "256k", "-ac", "2", "-ar", "44100"]},
    "mp4": {"ffmpeg_fmt": "mp4", "mime": "audio/mp4", "ext": ".m4a", "args": ["-vn", "-af", LOUDNORM_FILTER, "-c:a", "aac", "-b:a", "256k", "-ac", "2", "-ar", "44100", "-movflags", "+faststart"]},
    "wav": {"ffmpeg_fmt": "wav", "mime": "audio/wav", "ext": ".wav", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100"]},
    "flac": {"ffmpeg_fmt": "flac", "mime": "audio/flac", "ext": ".flac", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100"]},
    "ogg": {"ffmpeg_fmt": "ogg", "mime": "audio/ogg", "ext": ".ogg", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "7"]},
    "aac": {"ffmpeg_fmt": "adts", "mime": "audio/aac", "ext": ".aac", "args": ["-vn", "-af", LOUDNORM_FILTER, "-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "256k"]},
}


# The audio codec each target format must actually carry. A file named .mp3 can hold
# MP2 audio (160 of the station's old files did, 2026-11-17): Safari and the playout
# engine's MP3 decoder reject those, so the extension alone is not trusted.
EXPECTED_CODECS = {
    "mp3": {"mp3"},
    "mp2": {"mp2"},
    "mp4": {"aac"},
    "aac": {"aac"},
    "flac": {"flac"},
    "ogg": {"vorbis"},
}


def probe_codec(file_data: bytes, input_ext: str = ".bin") -> str | None:
    """The codec of the first audio stream (e.g. "mp3", "mp2"), or None if ffprobe
    cannot tell (missing, timed out, no audio stream)."""
    if not input_ext.startswith("."):
        input_ext = "." + input_ext
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as f:
            f.write(file_data)
            tmp_path = f.name
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
             "-select_streams", "a:0", tmp_path],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        streams = json.loads(result.stdout).get("streams") or []
        codec = streams[0].get("codec_name") if streams else None
        return str(codec).lower() if codec else None
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class UnreadableAudioError(ValueError):
    """The upload holds no audio FFmpeg can read (a PDF, an image, a broken file),
    or FFmpeg could not convert it. Nothing is stored; the caller answers 415."""


def _has_audio_stream(file_data: bytes, ext: str) -> bool | None:
    """True when ffprobe finds an audio stream, False when it finds none (or cannot
    read the file at all), None when ffprobe is missing so nothing can be told."""
    if not ext.startswith("."):
        ext = "." + ext
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(file_data)
            tmp_path = f.name
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
             "-select_streams", "a", tmp_path],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return False
        return bool(json.loads(result.stdout or b"{}").get("streams"))
    except FileNotFoundError:
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _ffmpeg_available() -> bool:
    import shutil

    return bool(shutil.which(settings.FFMPEG_PATH) or os.path.isfile(settings.FFMPEG_PATH))


def _codec_matches(file_data: bytes, ext: str, target_format: str) -> bool:
    """True when the file really carries the target's codec. When ffprobe cannot
    tell, the extension is trusted as before (no needless re-encode)."""
    if target_format == "wav":
        codec = probe_codec(file_data, ext)
        return codec is None or codec.startswith("pcm_")
    expected = EXPECTED_CODECS.get(target_format)
    if not expected:
        return True
    codec = probe_codec(file_data, ext)
    return codec is None or codec in expected


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
        Duration may be None if extraction fails.

    Raises:
        UnreadableAudioError: the file holds no readable audio, or FFmpeg failed to
        convert it. (Before 2026-11-17 the original bytes were stored with their own
        extension and the upload reported success: a PDF became a "song".) Only when
        ffprobe/FFmpeg are not installed at all are the original bytes kept as before.
    """
    ext = _get_extension(original_filename)

    # Diagnostic: log input file fingerprint
    input_hash = hashlib.md5(file_data[:4096]).hexdigest()
    logger.info(
        "convert_audio START: file='%s', ext='%s', target='%s', size=%d, hash_4k=%s",
        original_filename, ext, target_format, len(file_data), input_hash,
    )

    if _has_audio_stream(file_data, ext or ".bin") is False:
        logger.warning("Refusing '%s': no audio stream FFmpeg can read", original_filename)
        raise UnreadableAudioError(
            f"'{original_filename}' could not be read as audio. Upload an audio file (MP3, WAV, FLAC, M4A...)."
        )

    # "original" means no conversion
    if target_format == "original":
        logger.info("Keeping original format for '%s'", original_filename)
        duration = _extract_duration(file_data, ext)
        return file_data, duration, ext or ".bin"

    # Already in the target format — just extract duration
    fmt_config = CONVERT_FORMATS.get(target_format, CONVERT_FORMATS["mp3"])
    if ext == fmt_config["ext"]:
        if _codec_matches(file_data, ext, target_format):
            logger.info("File '%s' is already %s — skipping conversion", original_filename, target_format)
            duration = _extract_duration(file_data, ext)
            return file_data, duration, ext
        logger.warning(
            "File '%s' is named %s but its audio is not %s — converting",
            original_filename, ext, target_format,
        )

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

    if _ffmpeg_available():
        # FFmpeg ran and failed: storing the original would put a file of another
        # format (or a broken one) on air under a success message.
        raise UnreadableAudioError(
            f"'{original_filename}' could not be converted to {target_format.upper()}. Try another copy of the file."
        )

    # No FFmpeg on this machine at all: keep the original file as before
    logger.warning("Conversion failed for '%s' — storing original file as-is", original_filename)
    duration = _extract_duration(file_data, ext)
    return file_data, duration, ext or ".bin"


# Backwards-compatible alias
def convert_to_mp3(file_data: bytes, original_filename: str) -> tuple[bytes, float | None]:
    data, duration, _ext = convert_audio(file_data, original_filename, "mp3")
    return data, duration
