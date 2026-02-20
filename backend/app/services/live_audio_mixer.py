"""
Live audio mixer — stub for future ffmpeg-based host + caller audio mixing.

MVP: Host audio only goes to Icecast; caller audio stays in Twilio conference phone bridge.
Layer 2 enhancement: Use ffmpeg + Twilio Media Streams to combine host + caller audio
for listeners to hear both.
"""
import logging

logger = logging.getLogger(__name__)


async def mix_audio_streams():
    """Placeholder for future audio mixing functionality."""
    logger.debug("Audio mixer not implemented yet — MVP uses separate streams")
    pass
