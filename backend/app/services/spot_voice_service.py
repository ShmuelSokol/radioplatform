"""
Turns a sponsorship script into audio.

The real voice is Shprach (the station's own voice model, trained on the
Windows PC). Until that is reachable from the backend, `generate_spot_audio`
returns a placeholder: soft marker tones for as long as the script would take
to read. Everything downstream (storage, preview, approval, scheduling) is the
same either way, so wiring Shprach in means changing only `_shprach`.

Audio made elsewhere (e.g. rendered with Shprach on the PC) comes in through
the upload endpoint instead and never touches this module.
"""

import io
import logging
import math
import struct
import wave

from app.services.copy_review_service import estimate_seconds

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050


async def _shprach(script_text: str) -> tuple[bytes, float, str] | None:
    """Ask Shprach to read the script. Returns None while it is not connected."""
    return None


def placeholder_audio(seconds: float) -> bytes:
    """A WAV of the given length: a short quiet tone every two seconds, so a
    manager can hear that it is a stand-in and how long the spot will run."""
    seconds = max(1.0, min(float(seconds), 90.0))
    total = int(seconds * SAMPLE_RATE)
    frames = bytearray()
    for n in range(total):
        t = n / SAMPLE_RATE
        in_beep = (t % 2.0) < 0.25
        value = int(6000 * math.sin(2 * math.pi * 440 * t)) if in_beep else 0
        frames += struct.pack("<h", value)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue()


async def generate_spot_audio(script_text: str) -> tuple[bytes, float, str, str]:
    """Returns (audio bytes, seconds, voice, file extension)."""
    try:
        made = await _shprach(script_text)
    except Exception:
        logger.exception("Shprach generation failed; using placeholder audio")
        made = None
    if made is not None:
        data, seconds, ext = made
        return data, seconds, "shprach", ext
    seconds = round(estimate_seconds(script_text), 1)
    return placeholder_audio(seconds), max(1.0, min(seconds, 90.0)), "placeholder", ".wav"
