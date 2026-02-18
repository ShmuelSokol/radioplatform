import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def upload_to_supabase(file_data: bytes, path: str) -> str:
    """Upload a file to Supabase Storage and return the public URL.

    Args:
        file_data: Raw file bytes (e.g. MP3).
        path: Storage path within the bucket (e.g. "weather/2024-01-15T15-30_time.mp3").

    Returns:
        Public URL for the uploaded file.
    """
    bucket = settings.SUPABASE_STORAGE_BUCKET
    url = f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "audio/mpeg",
        "x-upsert": "true",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.put(url, content=file_data, headers=headers)
        resp.raise_for_status()

    public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
    return public_url
