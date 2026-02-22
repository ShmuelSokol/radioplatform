"""Audio enhancement / restoration via FFmpeg filters."""
import logging
import re
import subprocess
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Presets — each maps to a list of filter dicts
# ---------------------------------------------------------------------------
ENHANCEMENT_PRESETS: dict[str, list[dict]] = {
    "broadcast_polish": [
        {"name": "highpass", "params": {"frequency": 80}},
        {"name": "acompressor", "params": {"threshold": -20, "ratio": 4, "attack": 5, "release": 100}},
        {"name": "treble", "params": {"gain": 2, "frequency": 3000}},
        {"name": "loudnorm", "params": {"i": -16, "tp": -1.5, "lra": 11}},
    ],
    "noise_reduction": [
        {"name": "afftdn", "params": {"noise_floor": -25}},
    ],
    "clarity_boost": [
        {"name": "highpass", "params": {"frequency": 100}},
        {"name": "treble", "params": {"gain": 3, "frequency": 2500}},
        {"name": "acompressor", "params": {"threshold": -18, "ratio": 3, "attack": 10, "release": 150}},
    ],
    "warm_up": [
        {"name": "bass", "params": {"gain": 4, "frequency": 200}},
        {"name": "lowpass", "params": {"frequency": 12000}},
    ],
    "de_hiss": [
        {"name": "lowpass", "params": {"frequency": 10000}},
        {"name": "afftdn", "params": {"noise_floor": -20}},
    ],
}


def _validate_numeric(value: float | int, lo: float, hi: float, label: str) -> float:
    """Validate a numeric parameter is within range. Prevents command injection."""
    v = float(value)
    if not (lo <= v <= hi):
        raise ValueError(f"{label} must be between {lo} and {hi}, got {v}")
    return v


def _build_single_filter(f: dict) -> str:
    """Convert one filter dict to its FFmpeg string representation."""
    name = f["name"]
    p = f.get("params", {})

    if name == "afftdn":
        nf = _validate_numeric(p.get("noise_floor", -25), -80, 0, "noise_floor")
        return f"afftdn=nf={nf}"

    if name == "highpass":
        freq = _validate_numeric(p.get("frequency", 80), 10, 1000, "highpass frequency")
        return f"highpass=f={freq}"

    if name == "lowpass":
        freq = _validate_numeric(p.get("frequency", 10000), 1000, 22000, "lowpass frequency")
        return f"lowpass=f={freq}"

    if name == "bass":
        gain = _validate_numeric(p.get("gain", 3), -20, 20, "bass gain")
        freq = _validate_numeric(p.get("frequency", 200), 20, 1000, "bass frequency")
        return f"bass=g={gain}:f={freq}"

    if name == "treble":
        gain = _validate_numeric(p.get("gain", 3), -20, 20, "treble gain")
        freq = _validate_numeric(p.get("frequency", 3000), 1000, 16000, "treble frequency")
        return f"treble=g={gain}:f={freq}"

    if name == "acompressor":
        threshold = _validate_numeric(p.get("threshold", -20), -60, 0, "compressor threshold")
        ratio = _validate_numeric(p.get("ratio", 4), 1, 20, "compressor ratio")
        attack = _validate_numeric(p.get("attack", 5), 0.01, 2000, "compressor attack")
        release = _validate_numeric(p.get("release", 100), 1, 9000, "compressor release")
        return f"acompressor=threshold={threshold}dB:ratio={ratio}:attack={attack}:release={release}"

    if name == "loudnorm":
        i_val = _validate_numeric(p.get("i", -16), -70, -5, "loudnorm I")
        tp = _validate_numeric(p.get("tp", -1.5), -9, 0, "loudnorm TP")
        lra = _validate_numeric(p.get("lra", 11), 1, 20, "loudnorm LRA")
        return f"loudnorm=I={i_val}:TP={tp}:LRA={lra}"

    raise ValueError(f"Unknown filter: {name}")


def build_filter_chain(filters: list[dict]) -> str:
    """Convert a list of filter dicts to a single FFmpeg -af string."""
    if not filters:
        raise ValueError("At least one filter is required")
    parts = [_build_single_filter(f) for f in filters]
    return ",".join(parts)


def enhance_audio(
    file_data: bytes,
    filters: list[dict],
    input_ext: str = ".mp3",
) -> tuple[bytes, float]:
    """Apply full filter chain to audio. Returns (enhanced_bytes, duration)."""
    af_str = build_filter_chain(filters)
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", af_str,
        "-f", "mp3",
        "-ab", "192k",
        "pipe:1",
    ]
    logger.info("enhance_audio: %s", " ".join(cmd))
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=300)
    if result.returncode != 0:
        err = result.stderr[:1000].decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg enhance failed: {err}")

    # Probe duration from stderr
    duration = 0.0
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    # Look for the output duration
    matches = re.findall(r"time=(\d+):(\d+):(\d+)\.(\d+)", stderr_text)
    if matches:
        last = matches[-1]
        h, m, s, cs = int(last[0]), int(last[1]), int(last[2]), int(last[3])
        duration = h * 3600 + m * 60 + s + cs / 100.0

    return result.stdout, duration


def enhance_preview(
    file_data: bytes,
    filters: list[dict],
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
    input_ext: str = ".mp3",
) -> bytes:
    """Apply filters to a short segment and return MP3 blob for preview."""
    af_str = build_filter_chain(filters)
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-ss", str(start_seconds),
        "-t", str(duration_seconds),
        "-af", af_str,
        "-f", "mp3",
        "-ab", "128k",
        "pipe:1",
    ]
    logger.info("enhance_preview: %s", " ".join(cmd))
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=120)
    if result.returncode != 0:
        err = result.stderr[:1000].decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg enhance preview failed: {err}")

    return result.stdout


def _run_silence_detect(file_data: bytes, threshold_db: float, min_duration: float) -> list[dict[str, float]]:
    """Run FFmpeg silencedetect and return regions."""
    cmd = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, input=file_data, capture_output=True, timeout=120)
    stderr = result.stderr.decode("utf-8", errors="replace")

    regions: list[dict[str, float]] = []
    starts: list[float] = []
    for line in stderr.splitlines():
        sm = re.search(r"silence_start:\s*([\d.]+)", line)
        if sm:
            starts.append(float(sm.group(1)))
        em = re.search(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", line)
        if em and starts:
            s = starts.pop(0)
            regions.append({"start": s, "end": float(em.group(1)), "duration": float(em.group(2))})
    return regions


def detect_audience_segments(
    file_data: bytes,
    quiet_threshold_db: float = -25,
    silence_threshold_db: float = -45,
    min_duration: float = 1.0,
) -> list[dict[str, Any]]:
    """Detect segments where audience/students are speaking.

    Strategy: segments that appear as "silence" at a high threshold (quiet
    relative to the main speaker) but NOT at a low threshold (actual silence)
    are likely audience speech — students asking questions from a distance.

    Returns list of dicts: [{"start": float, "end": float, "duration": float}, ...]
    """
    # Pass 1: find "quiet" regions (below the speaker's normal level)
    quiet_regions = _run_silence_detect(file_data, quiet_threshold_db, min_duration)

    # Pass 2: find truly silent regions
    silent_regions = _run_silence_detect(file_data, silence_threshold_db, min_duration)

    # A quiet region is "audience speech" if it does NOT substantially overlap
    # with a truly silent region.
    audience: list[dict[str, Any]] = []
    for qr in quiet_regions:
        qs, qe = qr["start"], qr["end"]
        # Check overlap with any silent region
        is_silence = False
        for sr in silent_regions:
            ss, se = sr["start"], sr["end"]
            overlap_start = max(qs, ss)
            overlap_end = min(qe, se)
            overlap = max(0.0, overlap_end - overlap_start)
            # If more than 80% of the quiet region is also truly silent, skip it
            if overlap / max(qe - qs, 0.01) > 0.8:
                is_silence = True
                break
        if not is_silence:
            audience.append({
                "start": round(qs, 2),
                "end": round(qe, 2),
                "duration": round(qe - qs, 2),
            })

    return audience
