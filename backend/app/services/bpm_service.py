"""BPM detection service — estimates tempo from audio using FFmpeg + numpy.

Decodes audio to raw PCM via FFmpeg, then uses onset-strength autocorrelation
to estimate BPM.  Categorises into tempo buckets for playlist rotation.
"""
import logging
import os
import subprocess
import tempfile

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# Tempo → category mapping
TEMPO_CATEGORIES = [
    (0, 90, "relax"),       # slow / relaxing
    (90, 120, "med_fast"),  # medium / moderate
    (120, 999, "lively"),   # fast / lively
]


def _categorize_bpm(bpm: float) -> str:
    """Map a BPM value to a tempo category."""
    for lo, hi, cat in TEMPO_CATEGORIES:
        if lo <= bpm < hi:
            return cat
    return "med_fast"


def detect_bpm(audio_bytes: bytes, filename: str = "audio.mp3") -> tuple[float | None, str | None]:
    """Detect BPM from raw audio bytes.

    Uses FFmpeg to decode to mono 22050 Hz PCM, then computes onset-strength
    autocorrelation to find the dominant tempo.

    Returns:
        (bpm, category) — both None if detection fails.
    """
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    if not ext:
        ext = ".mp3"

    in_path = None
    try:
        # Write audio to temp file
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_bytes)
            in_path = f.name

        # Decode to raw 16-bit mono PCM at 22050 Hz
        result = subprocess.run(
            [
                settings.FFMPEG_PATH, "-i", in_path,
                "-f", "s16le", "-ac", "1", "-ar", "22050",
                "-v", "quiet", "pipe:1",
            ],
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0 or len(result.stdout) < 8192:
            logger.warning("FFmpeg decode for BPM failed (rc=%d, out=%d bytes)", result.returncode, len(result.stdout))
            return None, None

        # Convert raw bytes to numpy float array
        samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        sr = 22050

        if len(samples) < sr * 2:
            # Less than 2 seconds of audio — too short
            logger.info("Audio too short for BPM detection (%d samples)", len(samples))
            return None, None

        # Use only first 60 seconds to keep computation fast
        max_samples = sr * 60
        if len(samples) > max_samples:
            samples = samples[:max_samples]

        bpm = _estimate_bpm(samples, sr)
        if bpm is None:
            return None, None

        category = _categorize_bpm(bpm)
        logger.info("BPM detected: %.1f → category '%s' for '%s'", bpm, category, filename)
        return round(bpm, 1), category

    except FileNotFoundError:
        logger.warning("FFmpeg not found for BPM detection")
        return None, None
    except Exception:
        logger.warning("BPM detection failed for '%s'", filename, exc_info=True)
        return None, None
    finally:
        if in_path and os.path.exists(in_path):
            try:
                os.unlink(in_path)
            except OSError:
                pass


def _estimate_bpm(samples: np.ndarray, sr: int) -> float | None:
    """Estimate BPM using onset-strength autocorrelation.

    1. Compute short-time energy in overlapping frames
    2. Take the first derivative (onset strength)
    3. Autocorrelate to find dominant periodicity
    4. Convert lag to BPM
    """
    hop = 512
    frame_len = 1024

    # Compute frame energy
    n_frames = (len(samples) - frame_len) // hop
    if n_frames < 10:
        return None

    energy = np.zeros(n_frames)
    for i in range(n_frames):
        start = i * hop
        frame = samples[start:start + frame_len]
        energy[i] = np.sum(frame * frame)

    # Onset strength = positive first derivative of energy
    onset = np.diff(energy)
    onset = np.maximum(onset, 0.0)

    # Normalize
    mx = onset.max()
    if mx < 1e-8:
        return None
    onset = onset / mx

    # Autocorrelation
    frame_rate = sr / hop  # frames per second

    # BPM range: 50–200
    min_lag = int(frame_rate * 60 / 200)
    max_lag = int(frame_rate * 60 / 50)
    max_lag = min(max_lag, len(onset) - 1)

    if min_lag >= max_lag or max_lag <= 0:
        return None

    # Compute autocorrelation for the relevant lag range
    corr = np.zeros(max_lag - min_lag)
    for j, lag in enumerate(range(min_lag, max_lag)):
        n = len(onset) - lag
        if n <= 0:
            continue
        corr[j] = np.dot(onset[:n], onset[lag:lag + n])

    if corr.max() < 1e-8:
        return None

    best_idx = np.argmax(corr)
    best_lag = best_idx + min_lag
    bpm = frame_rate * 60.0 / best_lag

    # Sanity check
    if bpm < 40 or bpm > 220:
        return None

    return float(bpm)
