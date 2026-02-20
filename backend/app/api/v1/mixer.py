import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager
from app.db.session import get_db
from app.models.asset import Asset
from app.models.user import User
from app.schemas.asset import AssetResponse
from app.schemas.mixer import MixRequest
from app.services.asset_service import get_asset
from app.services.storage_service import generate_asset_key, upload_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio", tags=["studio"])


async def _download_asset_data(file_path: str) -> bytes:
    """Download asset data from URL or storage."""
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(file_path, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
    else:
        from app.services.storage_service import download_file
        return await download_file(file_path)


def _get_ext(file_path: str) -> str:
    """Extract file extension from path."""
    if "." in file_path:
        return "." + file_path.rsplit(".", 1)[-1].lower()
    return ".mp3"


@router.post("/mix", response_model=AssetResponse)
async def mix_tracks(
    body: MixRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Mix two assets (backtrack + overlay) and save as a new asset."""
    # Fetch both assets
    bt_asset = await get_asset(db, uuid.UUID(body.backtrack_asset_id))
    ov_asset = await get_asset(db, uuid.UUID(body.overlay_asset_id))

    # Download audio data
    bt_data = await _download_asset_data(bt_asset.file_path)
    ov_data = await _download_asset_data(ov_asset.file_path)

    bt_ext = _get_ext(bt_asset.file_path)
    ov_ext = _get_ext(ov_asset.file_path)

    # Mix
    from app.services.mixer_service import mix_audio

    mixed_data, duration = mix_audio(
        backtrack_data=bt_data,
        overlay_data=ov_data,
        bt_ext=bt_ext,
        ov_ext=ov_ext,
        bt_trim_start=body.bt_trim_start,
        bt_trim_end=body.bt_trim_end,
        bt_target_dur=body.bt_target_dur,
        bt_volume=body.bt_volume,
        ov_volume=body.ov_volume,
        bt_fade_in=body.bt_fade_in,
        bt_fade_out=body.bt_fade_out,
        bt_fade_out_start=body.bt_fade_out_start,
        ov_fade_in=body.ov_fade_in,
        ov_fade_out=body.ov_fade_out,
        ov_fade_out_start=body.ov_fade_out_start,
    )

    # Upload result
    s3_key = generate_asset_key("mixed.mp3")
    await upload_file(mixed_data, s3_key, "audio/mpeg")

    # Create new asset record
    asset = Asset(
        title=body.output_title,
        file_path=s3_key,
        duration=duration,
        created_by=user.id,
        asset_type=body.output_asset_type,
        category="studio",
        metadata_extra={
            "source": "mixer",
            "backtrack_asset_id": body.backtrack_asset_id,
            "overlay_asset_id": body.overlay_asset_id,
        },
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)

    logger.info("Mixed asset '%s' created: %s", body.output_title, asset.id)
    return asset
