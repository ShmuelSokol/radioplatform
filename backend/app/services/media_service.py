import asyncio
import json
import os
import tempfile

from app.config import settings


async def extract_metadata(file_path: str) -> dict:
    """Extract audio metadata using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(stdout)
        fmt = data.get("format", {})
        tags = fmt.get("tags", {})
        return {
            "duration": float(fmt.get("duration", 0)),
            "bitrate": int(fmt.get("bit_rate", 0)),
            "format_name": fmt.get("format_name", ""),
            "title": tags.get("title", ""),
            "artist": tags.get("artist", ""),
            "album": tags.get("album", ""),
            "genre": tags.get("genre", ""),
        }
    except (json.JSONDecodeError, ValueError):
        return {}


async def extract_album_art(file_path: str, output_path: str) -> bool:
    """Extract embedded album art to a file."""
    cmd = [
        settings.FFMPEG_PATH,
        "-i", file_path,
        "-an",
        "-vcodec", "mjpeg",
        "-vframes", "1",
        "-y",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0 and os.path.exists(output_path)


async def transcode_audio(
    input_path: str,
    output_path: str,
    codec: str = "aac",
    bitrate: str = "128k",
) -> bool:
    """Transcode audio to a target codec/bitrate."""
    cmd = [
        settings.FFMPEG_PATH,
        "-i", input_path,
        "-c:a", codec,
        "-b:a", bitrate,
        "-y",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def clip_audio(
    input_path: str,
    output_path: str,
    start_seconds: float,
    duration_seconds: float,
) -> bool:
    """Clip a section of an audio file."""
    cmd = [
        settings.FFMPEG_PATH,
        "-i", input_path,
        "-ss", str(start_seconds),
        "-t", str(duration_seconds),
        "-c", "copy",
        "-y",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def normalize_audio(input_path: str, output_path: str) -> bool:
    """Normalize audio loudness to -16 LUFS."""
    cmd = [
        settings.FFMPEG_PATH,
        "-i", input_path,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-y",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0
