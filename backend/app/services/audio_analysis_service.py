"""Audio analysis service — loudness measurement, cue point detection, and TTS normalization."""
import json
import logging
import re
import subprocess

from app.config import settings
from app.services.silence_service import detect_silence, get_audio_duration

logger = logging.getLogger(__name__)


def analyze_audio_bytes(file_data: bytes) -> dict:
    """Analyze audio bytes and return loudness + cue point data.

    Returns dict with: loudness_lufs, true_peak_dbfs, cue_in_seconds,
    cue_out_seconds, cross_start_seconds, replay_gain_db.
    """
    # 1. Measure loudness via FFmpeg loudnorm (first pass / print_format=json)
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", "loudnorm=I=-18:LRA=11:TP=-1.5:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=300)
    stderr = result.stderr.decode("utf-8", errors="replace")

    # Extract the JSON block from stderr (loudnorm prints it at end)
    loudness_lufs = -18.0
    true_peak_dbfs = -1.5
    json_match = re.search(r"\{[^}]*\"input_i\"[^}]*\}", stderr, re.DOTALL)
    if json_match:
        try:
            loudnorm_data = json.loads(json_match.group())
            loudness_lufs = float(loudnorm_data.get("input_i", -18.0))
            true_peak_dbfs = float(loudnorm_data.get("input_tp", -1.5))
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Failed to parse loudnorm JSON output")

    # 2. Get total duration
    total_duration = get_audio_duration(file_data)
    if total_duration <= 0:
        total_duration = 180.0  # fallback

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

    # cross_start: cue_out - 3.0, clamped to at least cue_in + 5
    cross_start = max(cue_in + 5.0, cue_out - 3.0)

    # replay_gain_db: -18.0 - measured_lufs, clamped to ±12 dB
    replay_gain_db = max(-12.0, min(12.0, -18.0 - loudness_lufs))

    return {
        "loudness_lufs": round(loudness_lufs, 2),
        "true_peak_dbfs": round(true_peak_dbfs, 2),
        "cue_in_seconds": round(cue_in, 3),
        "cue_out_seconds": round(cue_out, 3),
        "cross_start_seconds": round(cross_start, 3),
        "replay_gain_db": round(replay_gain_db, 2),
        "duration": round(total_duration, 3),
    }


async def analyze_audio(db, asset_id: str):
    """Download asset, analyze, and store results in metadata_extra['audio_analysis']."""
    from uuid import UUID
    from app.services.asset_service import get_asset

    aid = UUID(asset_id)
    asset = await get_asset(db, aid)
    file_path = asset.file_path

    # Download audio
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(file_path, follow_redirects=True)
            data = resp.content
    else:
        from app.services.storage_service import download_file
        data = await download_file(file_path)

    # Analyze
    analysis = analyze_audio_bytes(data)

    # Store in metadata_extra
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
