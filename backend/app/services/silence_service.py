"""Silence detection and audio trimming via FFmpeg."""
import logging
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# Audio to measure: the bytes themselves, or the path of a file holding them
AudioSource = bytes | str


def ffmpeg_input(src: AudioSource) -> tuple[list[str], bytes | None]:
    """FFmpeg's input arguments and stdin for `src`."""
    if isinstance(src, str):
        return ["-i", src], None
    return ["-i", "pipe:0"], src


@contextmanager
def audio_file(data: bytes, suffix: str = ".mp3"):
    """The bytes in a temporary file, for FFmpeg to read as a file and not a pipe.

    On a pipe FFmpeg cannot seek, so it gives up on a file whose audio starts after
    something it does not expect (stray bytes after the tags): "Invalid data found
    when processing input". The same file read from disk, or by ffprobe from its
    URL, plays and measures fine. Two good Carlebach stories (35 and 15 min) were
    taken off the air on 2026-11-18 because their length "could not be measured"
    from a pipe."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _decoded_length(src: AudioSource) -> float:
    args, stdin = ffmpeg_input(src)
    cmd = [settings.FFMPEG_PATH, *args, "-f", "null", "-"]
    result = subprocess.run(cmd, input=stdin, capture_output=True, timeout=300)
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


def _probed_length(path: str) -> float:
    """The length ffprobe reads from a file (its header or a scan), 0 when none."""
    probe = os.path.join(os.path.dirname(settings.FFMPEG_PATH), "ffprobe") if os.sep in settings.FFMPEG_PATH else "ffprobe"
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, timeout=120,
        )
        value = float(result.stdout.decode().strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0
    return value if value == value and value > 0 else 0.0


def get_audio_duration(file_data: AudioSource) -> float:
    """Length of the audio in seconds, from decoding it to the end.

    On a pipe ffmpeg usually cannot read a length from the header ("Duration: N/A"
    for MP3), so the decoder's last "time=" is the length. (Before this, every
    analysed item got the 180 s fallback as its length.) When the pipe gives
    nothing, the bytes are measured again from a file, and last by ffprobe, so a
    playable file is never called unmeasurable (see audio_file).
    """
    length = _decoded_length(file_data)
    if length > 0:
        return length
    if isinstance(file_data, str):
        return _probed_length(file_data)
    if not file_data:
        return 0.0
    with audio_file(file_data) as path:
        length = _decoded_length(path)
        if length <= 0:
            length = _probed_length(path)
    if length > 0:
        logger.info("Length read from a file after the pipe gave none: %.2f s", length)
    return length


def detect_silence(
    file_data: AudioSource,
    threshold_db: float = -30,
    min_duration: float = 0.5,
) -> list[dict[str, Any]]:
    """Run FFmpeg silencedetect filter and return silence regions.

    Returns a list of dicts: [{"start": float, "end": float, "duration": float}, ...]
    """
    args, stdin = ffmpeg_input(file_data)
    cmd = [
        settings.FFMPEG_PATH, *args,
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, input=stdin, capture_output=True, timeout=300)
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


def merge_regions(regions: list[dict], length: float | None = None) -> list[tuple[float, float]]:
    """Sorted, merged (start, end) spans, clipped to [0, length]; empty spans dropped."""
    spans = []
    for r in regions:
        try:
            a, b = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        a = max(0.0, a)
        if length:
            b = min(b, length)
        if b - a > 0.001:
            spans.append((a, b))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


EDGE = 0.05  # a gap within this of the start or end is the recording's lead-in or tail


def cut_spans(regions: list[dict], length: float | None, keep_gap: float = 0.0) -> list[tuple[float, float]]:
    """What to cut for these silent regions. Silence at the very start and end goes
    completely. A gap in the middle keeps `keep_gap` seconds (half each side), so the
    pause is shortened, not removed (Shmuel 2026-11-19: "replace the longer gaps with
    shorter gaps ... so it's not unnaturally short spaces"); a gap already that short
    stays as it is."""
    keep = max(0.0, keep_gap or 0.0)
    out = []
    for a, b in merge_regions(regions, length):
        at_start = a <= EDGE
        at_end = bool(length) and b >= length - EDGE
        if at_start or at_end or keep <= 0:
            out.append((a, b))
        elif b - a > keep + 0.01:
            out.append((a + keep / 2, b - keep / 2))
    return out


def remove_regions(file_data: bytes, regions: list[dict], progress=None,
                   keep_gap: float = 0.0) -> tuple[bytes, float]:
    """Cut every region out of the audio and join what is left, into an MP3.

    "Remove all found silence" in the Library (Shmuel 2026-11-18: "I like what it
    found. I want to delete it."). Gaps in the middle are removed too, not only at the
    start and end; with `keep_gap` each middle gap is shortened to that many seconds
    instead (see cut_spans). Read from a file, not a pipe (see audio_file). `progress(fraction,
    stage)` hears how far it is (job_progress.py).
    Returns (new_bytes, new_duration). Raises ValueError when nothing would be left."""
    from app.services import job_progress

    with audio_file(file_data) as path:
        if progress:
            progress(0.0, "Measuring the recording")
        length = get_audio_duration(path)
        spans = cut_spans(regions, length or None, keep_gap)
        if not spans:
            raise ValueError(f"Every gap is already {keep_gap:g} s or shorter" if keep_gap else "No silence to remove")
        removed = sum(b - a for a, b in spans)
        if length and length - removed < 0.5:
            raise ValueError("That would remove the whole recording")
        expr = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in spans)
        out = job_progress.temp_path(".mp3")
        try:
            code, stderr = job_progress.run_ffmpeg(
                ["-v", "error", "-y", "-i", path, "-vn",
                 "-af", f"aselect='not({expr})',asetpts=N/SR/TB",
                 "-c:a", "libmp3lame", "-b:a", "256k", "-ar", "44100", "-f", "mp3", out],
                max(length - removed, 0.0), job_progress.scaled(progress, 0.1, 1.0) if progress else None,
                "Removing the silence",
            )
            with open(out, "rb") as f:
                data = f.read()
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
        if code != 0 or not data:
            raise RuntimeError(f"FFmpeg could not remove the silence: {stderr[:500]!r}")
    return data, get_audio_duration(data)
