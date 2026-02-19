"""
Icecast source client — streams audio to an Icecast server for OTA broadcast.
Uses HTTP PUT (Icecast 2.4+) or libshout protocol to push audio data.
"""
import asyncio
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class IcecastSourceClient:
    """
    Streams audio to an Icecast server as a source client.
    Uses HTTP chunked transfer to push audio data to the mount point.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        mount: str | None = None,
        password: str | None = None,
        content_type: str | None = None,
    ):
        self.host = host or settings.ICECAST_HOST
        self.port = port or settings.ICECAST_PORT
        self.mount = mount or settings.ICECAST_MOUNT
        self.password = password or settings.ICECAST_SOURCE_PASSWORD
        self.content_type = content_type or (
            "audio/mpeg" if settings.ICECAST_FORMAT == "mp3" else "application/ogg"
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

    @property
    def source_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.mount}"

    async def start(self, station_name: str = "Kol Bramah"):
        """Start streaming to Icecast server."""
        if self._running:
            logger.warning("Icecast source already running")
            return

        if not settings.icecast_enabled:
            logger.warning("Icecast not configured (ICECAST_HOST is empty)")
            return

        self._running = True
        self._task = asyncio.create_task(self._stream_loop(station_name))
        logger.info(f"Icecast source started: {self.source_url}")

    async def stop(self):
        """Stop streaming."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Icecast source stopped")

    async def push_audio(self, data: bytes):
        """Push audio data to the stream queue."""
        if self._running:
            try:
                self._audio_queue.put_nowait(data)
            except asyncio.QueueFull:
                # Drop oldest frame if queue is full
                try:
                    self._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._audio_queue.put_nowait(data)

    async def _stream_loop(self, station_name: str):
        """Main streaming loop — connects to Icecast and pushes audio data."""
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    headers = {
                        "Content-Type": self.content_type,
                        "Ice-Name": station_name,
                        "Ice-Description": f"{station_name} Live Stream",
                        "Ice-Genre": "Various",
                        "Ice-Public": "1",
                        "Ice-Audio-Info": f"bitrate={settings.ICECAST_BITRATE}",
                    }

                    async def audio_stream():
                        while self._running:
                            try:
                                data = await asyncio.wait_for(
                                    self._audio_queue.get(), timeout=5.0
                                )
                                yield data
                            except asyncio.TimeoutError:
                                # Send silence/keepalive
                                continue

                    response = await client.put(
                        self.source_url,
                        headers=headers,
                        content=audio_stream(),
                        auth=("source", self.password),
                    )

                    if response.status_code != 200:
                        logger.error(
                            f"Icecast rejected connection: {response.status_code} {response.text}"
                        )

            except httpx.ConnectError:
                logger.error(f"Cannot connect to Icecast at {self.source_url}")
            except Exception as e:
                logger.error(f"Icecast streaming error: {e}")

            if self._running:
                logger.info("Reconnecting to Icecast in 5 seconds...")
                await asyncio.sleep(5)


# Global instance per station
_icecast_clients: dict[str, IcecastSourceClient] = {}


def get_icecast_client(station_id: str, mount: str | None = None) -> IcecastSourceClient:
    """Get or create an Icecast client for a station."""
    if station_id not in _icecast_clients:
        _icecast_clients[station_id] = IcecastSourceClient(
            mount=mount or f"/{station_id}"
        )
    return _icecast_clients[station_id]


async def start_icecast_stream(station_id: str, station_name: str, mount: str | None = None):
    """Start Icecast streaming for a station."""
    client = get_icecast_client(station_id, mount)
    await client.start(station_name)


async def stop_icecast_stream(station_id: str):
    """Stop Icecast streaming for a station."""
    if station_id in _icecast_clients:
        await _icecast_clients[station_id].stop()
        del _icecast_clients[station_id]
