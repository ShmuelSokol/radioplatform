"""
Async client for communicating with Liquidsoap over TCP telnet.

Uses the Liquidsoap telnet protocol: send a command, read the response, close.
Graceful degradation: if host is not configured or connection fails, returns None.
"""
import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def _send_command(command: str) -> str | None:
    """Send a single command to Liquidsoap via TCP telnet."""
    host = settings.LIQUIDSOAP_HOST
    port = settings.LIQUIDSOAP_TELNET_PORT
    if not host:
        return None

    try:
        reader, writer = await asyncio.open_connection(host, port)
        writer.write((command + "\n").encode())
        await writer.drain()

        # Read response until END marker or connection closes
        response_lines = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line:
                break
            decoded = line.decode().strip()
            if decoded == "END":
                break
            response_lines.append(decoded)

        writer.close()
        await writer.wait_closed()

        response = "\n".join(response_lines)
        logger.debug("Liquidsoap command '%s' -> '%s'", command, response[:100])
        return response
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
        logger.warning("Liquidsoap command failed (%s:%d): %s", host, port, e)
        return None
    except Exception as e:
        logger.warning("Liquidsoap command failed: %s", e)
        return None


async def push_track(audio_url: str, station_id: str = "default") -> str | None:
    """Push a track URL to Liquidsoap's request queue."""
    if not settings.liquidsoap_enabled:
        return None
    return await _send_command(f"main_queue.push {audio_url}")


async def skip(station_id: str = "default") -> str | None:
    """Skip the currently playing track in Liquidsoap."""
    if not settings.liquidsoap_enabled:
        return None
    return await _send_command("main_queue.skip")


async def status() -> str | None:
    """Query the Liquidsoap queue status."""
    if not settings.liquidsoap_enabled:
        return None
    return await _send_command("main_queue.queue")


async def is_alive() -> bool:
    """Check if Liquidsoap is responding."""
    if not settings.liquidsoap_enabled:
        return False
    result = await _send_command("version")
    return result is not None
