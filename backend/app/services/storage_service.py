import uuid
from io import BytesIO

import aioboto3

from app.config import settings


def _get_session():
    return aioboto3.Session()


async def upload_file(
    file_data: bytes,
    key: str,
    content_type: str = "audio/mpeg",
) -> str:
    session = _get_session()
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
    return key


async def download_file(key: str) -> bytes:
    session = _get_session()
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


async def delete_file(key: str) -> None:
    session = _get_session()
    async with session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    ) as s3:
        await s3.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)


async def get_presigned_url(key: str, expires_in: int = 3600) -> str:
    session = _get_session()
    async with session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    ) as s3:
        url = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in,
        )
        return url


async def ensure_bucket_exists() -> None:
    session = _get_session()
    async with session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    ) as s3:
        try:
            await s3.head_bucket(Bucket=settings.S3_BUCKET_NAME)
        except Exception:
            await s3.create_bucket(Bucket=settings.S3_BUCKET_NAME)


def generate_asset_key(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "mp3"
    return f"assets/{uuid.uuid4()}.{ext}"


def generate_art_key(asset_id: str) -> str:
    return f"artwork/{asset_id}.jpg"
