"""Does a recording hold EAS signals?

Airing the EAS header codes (SAME), the end-of-message codes or the two-tone attention
signal outside a real alert or an authorised test breaks 47 CFR 11.45, on the air and on
a stream. So a recording a person uploads for a test of our override is checked first,
and one that holds them is refused (see wiki/emergency-alerts.md).

  * attention signal: 853 Hz and 960 Hz together, for at least a second (it runs 8 to 25 s)
  * SAME data bursts: AFSK at 520.83 baud, mark 2083.3 Hz and space 1562.5 Hz; nearly all
    the sound inside 1.45 to 2.2 kHz for at least 0.3 s (an end-of-message burst is 0.31 s)

Speech and music spread their sound wider, so neither counts. A plain whistle at about
1.8 kHz would: it is refused too, which is the safe side.
"""

from __future__ import annotations

import asyncio
import subprocess

import numpy as np

RATE = 8000


def _decode_wav(data: bytes) -> np.ndarray | None:
    """Plain 16-bit PCM WAV read without ffmpeg (None for anything else)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    import io
    import wave

    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                return None
            rate, channels = w.getframerate(), w.getnchannels()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    except (wave.Error, EOFError):
        return None
    if channels > 1:
        pcm = pcm[: len(pcm) - len(pcm) % channels].reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(pcm):
        # Linear resampling: plenty for a band check (the bands sit far below 4 kHz)
        n = int(len(pcm) * RATE / rate)
        pcm = np.interp(np.arange(n) * rate / RATE, np.arange(len(pcm)), pcm)
    return pcm


def decode(data: bytes) -> np.ndarray:
    """Mono samples at RATE. WAV is read directly; anything else goes through ffmpeg."""
    pcm = _decode_wav(data)
    if pcm is not None:
        return pcm
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"],
        input=data, capture_output=True, timeout=60,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise ValueError("The audio could not be read")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64) / 32768.0


def _band(power: np.ndarray, freqs: np.ndarray, lo: float, hi: float) -> float:
    return float(power[(freqs >= lo) & (freqs <= hi)].sum())


def find_in_samples(x: np.ndarray, rate: int = RATE) -> dict:
    """Seconds into the audio where each kind of EAS signal was found (empty lists = none)."""
    out = {"attention_at": [], "same_at": []}
    win = int(rate * 0.1)
    if len(x) < win:
        return out
    freqs = np.fft.rfftfreq(win, 1 / rate)
    hann = np.hanning(win)
    att_run = same_run = 0
    att_seen = False
    for i in range(0, len(x) - win + 1, win):
        seg = x[i:i + win]
        if np.sqrt(np.mean(seg ** 2)) < 0.003:  # quieter than -50 dBFS: nothing there
            att_run = same_run = 0
            continue
        power = np.abs(np.fft.rfft(seg * hann)) ** 2
        total = float(power.sum()) + 1e-12
        a853 = _band(power, freqs, 833, 873) / total
        a960 = _band(power, freqs, 940, 980) / total
        same = _band(power, freqs, 1450, 2200) / total
        # Both tones, nearly all the sound (a phone speaker loses some of the 853 Hz one)
        att_run = att_run + 1 if a853 > 0.04 and a960 > 0.04 and a853 + a960 > 0.45 else max(0, att_run - 3)
        same_run = same_run + 1 if same > 0.85 else 0
        t = round(i / rate, 1)
        if att_run >= 10 and not att_seen:  # about 1 s
            att_seen = True
            out["attention_at"].append(round(t - 0.9, 1))
        if att_run == 0:
            att_seen = False
        if same_run == 3:  # 0.3 s
            out["same_at"].append(round(t - 0.2, 1))
    return out


def find(data: bytes) -> dict:
    found = find_in_samples(decode(data))
    found["has_tones"] = bool(found["attention_at"] or found["same_at"])
    return found


async def find_async(data: bytes) -> dict:
    return await asyncio.to_thread(find, data)
