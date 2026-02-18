import json
import uuid

import redis.asyncio as aioredis

from app.config import settings


class PlaylistEngine:
    """Redis-backed play queue for a station."""

    def __init__(self, station_id: str):
        self.station_id = station_id
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(settings.REDIS_URL)
        return self._redis

    @property
    def _queue_key(self) -> str:
        return f"station:{self.station_id}:queue"

    @property
    def _now_playing_key(self) -> str:
        return f"station:{self.station_id}:now_playing"

    @property
    def _state_key(self) -> str:
        return f"station:{self.station_id}:state"

    async def enqueue(self, asset_id: str, title: str, file_path: str, duration: float = 0) -> None:
        r = await self._get_redis()
        item = json.dumps({
            "asset_id": asset_id,
            "title": title,
            "file_path": file_path,
            "duration": duration,
        })
        await r.rpush(self._queue_key, item)

    async def dequeue(self) -> dict | None:
        r = await self._get_redis()
        item = await r.lpop(self._queue_key)
        if item:
            return json.loads(item)
        return None

    async def peek_queue(self, count: int = 10) -> list[dict]:
        r = await self._get_redis()
        items = await r.lrange(self._queue_key, 0, count - 1)
        return [json.loads(i) for i in items]

    async def queue_length(self) -> int:
        r = await self._get_redis()
        return await r.llen(self._queue_key)

    async def set_now_playing(self, asset_info: dict) -> None:
        r = await self._get_redis()
        await r.set(self._now_playing_key, json.dumps(asset_info))

    async def get_now_playing(self) -> dict | None:
        r = await self._get_redis()
        data = await r.get(self._now_playing_key)
        if data:
            return json.loads(data)
        return None

    async def clear_now_playing(self) -> None:
        r = await self._get_redis()
        await r.delete(self._now_playing_key)

    async def set_state(self, state: str) -> None:
        """Set station playback state: playing, paused, stopped."""
        r = await self._get_redis()
        await r.set(self._state_key, state)

    async def get_state(self) -> str:
        r = await self._get_redis()
        state = await r.get(self._state_key)
        return state.decode() if state else "stopped"

    async def clear_queue(self) -> None:
        r = await self._get_redis()
        await r.delete(self._queue_key)

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None
