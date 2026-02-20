"""MusicBrainz release date lookup service."""
import asyncio
import logging
from datetime import date

import httpx

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "RadioPlatform/1.0 (contact@kolbramah.com)"
MIN_SCORE = 80

# Rate limit: 1 request per second
_last_request_time: float = 0
_lock = asyncio.Lock()


async def _rate_limit():
    """Enforce 1 req/sec rate limit for MusicBrainz API."""
    global _last_request_time
    async with _lock:
        now = asyncio.get_event_loop().time()
        elapsed = now - _last_request_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_request_time = asyncio.get_event_loop().time()


def _parse_date(date_str: str) -> date | None:
    """Parse a MusicBrainz date string (YYYY, YYYY-MM, or YYYY-MM-DD)."""
    if not date_str:
        return None
    parts = date_str.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


async def lookup_release_date(title: str, artist: str | None = None) -> date | None:
    """Look up the first release date for a recording on MusicBrainz.

    Returns a date object if found with sufficient confidence, else None.
    """
    query_parts = [f'recording:"{title}"']
    if artist:
        query_parts.append(f'artist:"{artist}"')
    query = " AND ".join(query_parts)

    await _rate_limit()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                MB_BASE,
                params={"query": query, "fmt": "json", "limit": 3},
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("MusicBrainz lookup failed for '%s': %s", title, e)
        return None

    recordings = data.get("recordings", [])
    if not recordings:
        return None

    # Find the best match with score > MIN_SCORE
    for rec in recordings:
        score = rec.get("score", 0)
        if score < MIN_SCORE:
            continue
        frd = rec.get("first-release-date")
        if frd:
            parsed = _parse_date(frd)
            if parsed:
                logger.info(
                    "MusicBrainz match for '%s' (score=%d): release_date=%s",
                    title, score, parsed,
                )
                return parsed

    return None
