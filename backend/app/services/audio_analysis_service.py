"""Audio analysis service — loudness measurement, cue point detection, and TTS normalization."""
import json
import logging
import re
import subprocess

from app.config import settings
from app.models.asset import ANALYSIS_VERSION, replay_gain_for  # noqa: F401 (re-exported)
from app.services.silence_service import AudioSource, audio_file, detect_silence, ffmpeg_input, get_audio_duration

logger = logging.getLogger(__name__)



def measure_loudness(data: AudioSource) -> tuple[float | None, float | None]:
    """(integrated LUFS, true peak dBFS) of some audio (bytes or a file path), from
    FFmpeg loudnorm's measuring pass. (None, None) when it cannot be measured."""
    args, stdin = ffmpeg_input(data)
    cmd = [
        settings.FFMPEG_PATH, "-hide_banner", *args,
        "-af", "loudnorm=I=-18:LRA=11:TP=-1.5:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, input=stdin, capture_output=True, timeout=300)
    stderr = result.stderr.decode("utf-8", errors="replace")
    json_match = re.search(r"\{[^}]*\"input_i\"[^}]*\}", stderr, re.DOTALL)
    if not json_match:
        return None, None
    try:
        loudnorm_data = json.loads(json_match.group())
        lufs, tp = float(loudnorm_data["input_i"]), float(loudnorm_data["input_tp"])
    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
        logger.warning("Failed to parse loudnorm JSON output")
        return None, None
    if lufs != lufs or lufs in (float("inf"), float("-inf")):
        return None, None
    return lufs, tp


def analyze_audio_bytes(file_data: bytes) -> dict:
    """Analyze audio bytes and return loudness + cue point data.

    Returns dict with: loudness_lufs, true_peak_dbfs, cue_in_seconds,
    cue_out_seconds, cross_start_seconds, replay_gain_db.

    FFmpeg reads the audio from a temporary file, not a pipe: on a pipe it cannot
    seek and gave up on good files (silence_service.audio_file).
    """
    with audio_file(file_data) as path:
        return _analyze_file(path)


def _analyze_file(file_data: str) -> dict:
    # 1. Measure loudness via FFmpeg loudnorm (first pass / print_format=json)
    loudness_lufs, true_peak_dbfs = measure_loudness(file_data)
    if loudness_lufs is None:
        loudness_lufs, true_peak_dbfs = -18.0, -1.5

    # 2. Get total duration
    total_duration = get_audio_duration(file_data)
    if total_duration <= 0:
        # Never guess: a made-up 180 s length put every item's cue and crossfade
        # points at 177-180 s whatever its real length.
        raise ValueError("Could not measure the length of the audio")

    # 3. Detect silence for cue points
    regions = detect_silence(file_data, threshold_db=-30, min_duration=0.5)

    # 4. Calculate cue points
    cue_in = 0.0
    cue_out = total_duration

    # cue_in: end of first silence region if it starts at ~0
    if regions and regions[0]["start"] < 0.05:
        cue_in = regions[0]["end"]

    # cue_out: start of last silence region if it ends at ~total_duration
    if regions and abs(regions[-1]["end"] - total_duration) < 0.5:
        cue_out = regions[-1]["start"]

    # A file that is silent from start to end (the blackout's silence): no cues at all.
    # The rules above put the start cue at the end and the end cue at 0, and the item
    # ended the moment it started (Shabbos 2026-11-20).
    if cue_in >= cue_out - 0.5:
        cue_in, cue_out = 0.0, total_duration

    # cross_start: cue_out - 3.0, clamped to at least cue_in + 5
    cross_start = max(cue_in + 5.0, cue_out - 3.0)

    # replay_gain_db: -18.0 - measured_lufs, limited to -20..+12 dB (models/asset.py)
    replay_gain_db = replay_gain_for(loudness_lufs)

    return {
        "loudness_lufs": round(loudness_lufs, 2),
        "true_peak_dbfs": round(true_peak_dbfs, 2),
        "cue_in_seconds": round(cue_in, 3),
        "cue_out_seconds": round(cue_out, 3),
        "cross_start_seconds": round(cross_start, 3),
        "replay_gain_db": round(replay_gain_db, 2),
        "duration": round(total_duration, 3),
        "version": ANALYSIS_VERSION,
    }


async def analyze_audio(db, asset_id: str, full: bool = False):
    """Analyse an item and store the result in metadata_extra['audio_analysis'].

    By default only a few ranges of the file are read (services/tail_analysis.py:
    header, last seconds, and the start only if no start cue is stored), keeping
    stored loudness. `full=True` downloads and measures the whole file; the upload
    path uses it, since that file was just sent to us anyway."""
    from uuid import UUID
    from app.services.asset_service import get_asset

    aid = UUID(asset_id)
    asset = await get_asset(db, aid)
    file_path = asset.file_path
    stored_extra = asset.metadata_extra
    # End the read transaction before the slow part (network reads, FFmpeg). An
    # open transaction keeps a lock on the assets table, and a schema change
    # queued behind it then blocks every other query (froze a deploy once).
    await db.commit()

    analysis = None
    if not full:
        from app.services.tail_analysis import LoudnessNotSampledError, analyze_by_range, public_audio_url

        url = public_audio_url(file_path or "")
        if not url:
            raise ValueError(f"No public URL for asset {asset_id}")
        previous = stored_extra.get("audio_analysis") if isinstance(stored_extra, dict) else None
        try:
            analysis = await analyze_by_range(url, previous if isinstance(previous, dict) else None)
        except LoudnessNotSampledError as e:
            logger.warning("Asset %s: %s; measuring the whole file", asset_id, e)
    if analysis is None:
        # Download audio
        if file_path.startswith("http://") or file_path.startswith("https://"):
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(file_path, follow_redirects=True)
                resp.raise_for_status()
                data = resp.content
        else:
            from app.services.storage_service import download_file
            data = await download_file(file_path)

        # Analyse off the event loop: ffmpeg decodes the whole file three times, and the
        # scheduler and every listener's connection share this loop.
        import asyncio
        analysis = await asyncio.to_thread(analyze_audio_bytes, data)

    # Store in metadata_extra, in a short transaction of its own (re-read, since the
    # row may have changed while we were measuring)
    asset = await get_asset(db, aid)
    extra = dict(asset.metadata_extra or {})
    extra["audio_analysis"] = analysis
    asset.metadata_extra = extra

    # Also update duration if it differs significantly
    if analysis["duration"] > 0 and (not asset.duration or abs(asset.duration - analysis["duration"]) > 1.0):
        asset.duration = analysis["duration"]

    await db.flush()
    logger.info("Audio analysis complete for asset %s: lufs=%.1f, cue_in=%.2f, cue_out=%.2f",
                asset_id, analysis["loudness_lufs"], analysis["cue_in_seconds"], analysis["cue_out_seconds"])
    return analysis


def normalize_audio_loudness(audio_bytes: bytes, target_lufs: float = -18.0) -> bytes:
    """Single-pass FFmpeg loudnorm filter for TTS normalization. Returns normalized bytes."""
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", f"loudnorm=I={target_lufs}:LRA=11:TP=-1.5",
        "-f", "mp3", "-ab", "192k",
        "pipe:1",
    ]
    result = subprocess.run(cmd, input=audio_bytes, capture_output=True, timeout=120)
    if result.returncode != 0:
        logger.warning("Loudness normalization failed, returning original: %s", result.stderr[:300])
        return audio_bytes
    return result.stdout
