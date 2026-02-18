import os
import uuid

from app.config import settings

# Local storage fallback directory
LOCAL_STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")


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
    else:
        path = os.path.join(LOCAL_STORAGE_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(file_data)
    return key


async def download_file(key: str) -> bytes:
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
    else:
        path = os.path.join(LOCAL_STORAGE_DIR, key)
        if os.path.exists(path):
            os.remove(path)


def generate_asset_key(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "mp3"
    return f"assets/{uuid.uuid4()}.{ext}"


def generate_art_key(asset_id: str) -> str:
    return f"artwork/{asset_id}.jpg"
