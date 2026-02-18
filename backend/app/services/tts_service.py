import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def generate_tts(text: str) -> tuple[bytes, float]:
    """Generate speech audio from text using ElevenLabs TTS.

    Returns (mp3_bytes, estimated_duration_seconds).
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        mp3_bytes = resp.content

    # Estimate duration: MP3 at ~128kbps
    estimated_duration = len(mp3_bytes) * 8 / 128000
    return mp3_bytes, estimated_duration
