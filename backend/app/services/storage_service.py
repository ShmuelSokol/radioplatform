import logging
import os
import uuid

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Local storage fallback directory
LOCAL_STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")


async def _supabase_upload(file_data: bytes, key: str, content_type: str) -> None:
    bucket = settings.SUPABASE_STORAGE_BUCKET
    url = f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(url, content=file_data, headers=headers)
        resp.raise_for_status()
    logger.info("Uploaded %s to Supabase Storage (%d bytes)", key, len(file_data))


async def _supabase_download(key: str) -> bytes:
    bucket = settings.SUPABASE_STORAGE_BUCKET
    url = f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    headers = {"Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


async def _supabase_delete(key: str) -> None:
    bucket = settings.SUPABASE_STORAGE_BUCKET
    url = f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    headers = {"Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers=headers)
        if resp.status_code not in (200, 404):
            resp.raise_for_status()


async def upload_file(
    file_data: bytes,
    key: str,
    content_type: str = "audio/mpeg",
) -> str:
    if settings.s3_enabled:
        import aioboto3
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
        ) as s3:
            await s3.put_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=key,
                Body=file_data,
                ContentType=content_type,
            )
    elif settings.supabase_storage_enabled:
        await _supabase_upload(file_data, key, content_type)
    else:
        path = os.path.join(LOCAL_STORAGE_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(file_data)
    return key


async def download_file(key: str) -> bytes:
    # If key is already a full URL (e.g. weather/time TTS assets store
    # the Supabase public URL directly), fetch it via HTTP instead of
    # constructing a storage-API path which would double-wrap the URL.
    if key.startswith("http://") or key.startswith("https://"):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(key, follow_redirects=True)
            resp.raise_for_status()
            return resp.content

    if settings.s3_enabled:
        import aioboto3
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
        ) as s3:
            response = await s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
            data = await response["Body"].read()
            return data
    elif settings.supabase_storage_enabled:
        return await _supabase_download(key)
    else:
        path = os.path.join(LOCAL_STORAGE_DIR, key)
        with open(path, "rb") as f:
            return f.read()


async def delete_file(key: str) -> None:
    if settings.s3_enabled:
        import aioboto3
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
        ) as s3:
            await s3.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
    elif settings.supabase_storage_enabled:
        await _supabase_delete(key)
    else:
        path = os.path.join(LOCAL_STORAGE_DIR, key)
        if os.path.exists(path):
            os.remove(path)


def generate_asset_key(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "mp3"
    return f"assets/{uuid.uuid4()}.{ext}"


def generate_art_key(asset_id: str) -> str:
    return f"artwork/{asset_id}.jpg"
