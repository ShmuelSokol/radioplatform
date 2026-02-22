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
    "bbe_sonic_maximizer": [
        {"name": "highpass", "params": {"frequency": 40}},
        {"name": "treble", "params": {"gain": 3, "frequency": 3000}},
        {"name": "bass", "params": {"gain": 2, "frequency": 250}},
        {"name": "acompressor", "params": {"threshold": -15, "ratio": 2, "attack": 3, "release": 80}},
    ],
    "voice_boost": [
        {"name": "highpass", "params": {"frequency": 100}},
        {"name": "equalizer", "params": {"frequency": 2500, "width": 2.0, "gain": 6}},
        {"name": "acompressor", "params": {"threshold": -30, "ratio": 8, "attack": 3, "release": 80}},
        {"name": "acompressor", "params": {"threshold": -18, "ratio": 3, "attack": 10, "release": 200}},
        {"name": "loudnorm", "params": {"i": -14, "tp": -1, "lra": 7}},
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

    if name == "equalizer":
        freq = _validate_numeric(p.get("frequency", 1000), 20, 20000, "equalizer frequency")
        width = _validate_numeric(p.get("width", 1.0), 0.01, 10, "equalizer width")
        gain = _validate_numeric(p.get("gain", 0), -20, 20, "equalizer gain")
        return f"equalizer=f={freq}:width_type=o:w={width}:g={gain}"

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


# ---------------------------------------------------------------------------
# AI Auto-Enhancement — analyze audio, then build optimal filter chain
# ---------------------------------------------------------------------------

def analyze_audio(file_data: bytes) -> dict[str, Any]:
    """Analyze audio characteristics using FFmpeg volumedetect + astats.

    Returns metrics: mean_volume, max_volume, noise_floor_est, dynamic_range, etc.
    """
    # Run volumedetect
    cmd_vol = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    res_vol = subprocess.run(cmd_vol, input=file_data, capture_output=True, timeout=120)
    stderr_vol = res_vol.stderr.decode("utf-8", errors="replace")

    stats: dict[str, Any] = {}

    # Parse volumedetect output
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", stderr_vol)
    stats["mean_volume"] = float(m.group(1)) if m else -20.0

    m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", stderr_vol)
    stats["max_volume"] = float(m.group(1)) if m else 0.0

    # Run astats for more detailed analysis (RMS, peak, crest factor, noise floor)
    cmd_stats = [
        settings.FFMPEG_PATH, "-i", "pipe:0",
        "-af", "astats=metadata=1:reset=0",
        "-f", "null", "-",
    ]
    res_stats = subprocess.run(cmd_stats, input=file_data, capture_output=True, timeout=120)
    stderr_stats = res_stats.stderr.decode("utf-8", errors="replace")

    # Parse astats — look for Overall RMS level and other metrics
    m = re.search(r"RMS level dB:\s*([-\d.]+)", stderr_stats)
    stats["rms_level"] = float(m.group(1)) if m else stats["mean_volume"]

    m = re.search(r"Peak level dB:\s*([-\d.]+)", stderr_stats)
    stats["peak_level"] = float(m.group(1)) if m else stats["max_volume"]

    m = re.search(r"Noise floor dB:\s*([-\d.]+)", stderr_stats)
    stats["noise_floor"] = float(m.group(1)) if m else -60.0

    m = re.search(r"Crest factor:\s*([-\d.]+)", stderr_stats)
    stats["crest_factor"] = float(m.group(1)) if m else 10.0

    m = re.search(r"Dynamic range:\s*([-\d.]+)", stderr_stats)
    stats["dynamic_range"] = float(m.group(1)) if m else 20.0

    m = re.search(r"Flat factor:\s*([-\d.]+)", stderr_stats)
    stats["flat_factor"] = float(m.group(1)) if m else 0.0

    # Compute derived metrics
    stats["headroom"] = abs(stats["max_volume"])  # dB below 0
    stats["loudness_range"] = abs(stats["max_volume"] - stats["mean_volume"])

    logger.info("Audio analysis: %s", stats)
    return stats


def build_auto_filters(stats: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """Given audio analysis stats, build the optimal filter chain.

    Returns (filters, reasons) — the filter list and human-readable explanations.
    """
    filters: list[dict] = []
    reasons: list[str] = []

    noise_floor = stats.get("noise_floor", -60.0)
    mean_vol = stats.get("mean_volume", -20.0)
    max_vol = stats.get("max_volume", 0.0)
    headroom = stats.get("headroom", 0.0)
    loudness_range = stats.get("loudness_range", 20.0)
    dynamic_range = stats.get("dynamic_range", 20.0)

    # 1. Noise reduction — if noise floor is high (above -50dB)
    if noise_floor > -50:
        # Set denoise floor slightly above the detected noise floor
        denoise_level = max(noise_floor + 5, -40)
        denoise_level = min(denoise_level, -10)
        filters.append({"name": "afftdn", "params": {"noise_floor": round(denoise_level, 1)}})
        reasons.append(f"Noise reduction: noise floor at {noise_floor:.0f}dB is high, denoising at {denoise_level:.0f}dB")

    # 2. High-pass filter — always good to remove sub-bass rumble
    # More aggressive if noise floor is high
    hpf_freq = 80 if noise_floor < -50 else 120
    filters.append({"name": "highpass", "params": {"frequency": hpf_freq}})
    reasons.append(f"High-pass filter at {hpf_freq}Hz to remove rumble")

    # 3. Bass boost — if audio sounds thin (mean volume very low, or high noise floor
    #    suggests it was recorded poorly)
    if mean_vol < -25:
        filters.append({"name": "bass", "params": {"gain": 2, "frequency": 200}})
        reasons.append("Gentle bass boost: audio appears thin")

    # 4. Treble / clarity — add presence for intelligibility
    treble_gain = 2
    if noise_floor > -45:
        treble_gain = 1  # Less treble boost if noisy (would amplify hiss)
    elif mean_vol < -22:
        treble_gain = 3  # More boost for quiet recordings that need clarity
    filters.append({"name": "treble", "params": {"gain": treble_gain, "frequency": 3000}})
    reasons.append(f"Treble boost +{treble_gain}dB at 3kHz for clarity")

    # 5. Compression — if dynamic range is too wide (>15dB difference between
    #    mean and peak, or high crest factor)
    if loudness_range > 12 or dynamic_range > 25:
        ratio = 3 if loudness_range > 18 else 2.5
        threshold = max(mean_vol + 5, -30)
        threshold = min(threshold, -8)
        filters.append({"name": "acompressor", "params": {
            "threshold": round(threshold, 1),
            "ratio": ratio,
            "attack": 8,
            "release": 120,
        }})
        reasons.append(f"Compression (ratio {ratio}:1): dynamic range is {loudness_range:.0f}dB")

    # 6. Loudness normalization — always apply to get broadcast-standard levels
    target_lufs = -16  # Standard broadcast level
    filters.append({"name": "loudnorm", "params": {"i": target_lufs, "tp": -1.5, "lra": 11}})
    reasons.append(f"Loudness normalization to {target_lufs} LUFS (broadcast standard)")

    return filters, reasons


def auto_enhance(file_data: bytes, input_ext: str = ".mp3") -> tuple[bytes, float, list[dict], list[str]]:
    """Analyze audio and apply optimal enhancement automatically.

    Returns (enhanced_bytes, duration, filters_applied, reasons).
    """
    stats = analyze_audio(file_data)
    filters, reasons = build_auto_filters(stats)
    enhanced_data, duration = enhance_audio(file_data, filters, input_ext)
    return enhanced_data, duration, filters, reasons
