import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_manager
from app.db.session import get_db
from app.models.user import User
from app.schemas.asset import (
    AssetListResponse,
    AssetResponse,
    AssetUpdate,
    ClipRequest,
    TaskStatusResponse,
    TranscodeRequest,
)
from app.services.asset_service import create_asset, delete_asset, get_asset, list_assets
from app.workers.tasks.media_tasks import task_clip_audio, task_extract_metadata, task_transcode_audio

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("/upload", response_model=AssetResponse, status_code=201)
async def upload_asset(
    file: UploadFile = File(...),
    title: str = Form(...),
    format: str = Form("mp3"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    file_data = await file.read()
    original_filename = file.filename or "upload.mp3"
    asset = await create_asset(
        db,
        title=title,
        filename=original_filename,
        file_data=file_data,
        content_type=file.content_type or "audio/mpeg",
        user_id=user.id,
        original_filename=original_filename,
        target_format=format,
    )
    # Dispatch metadata extraction task
    task_extract_metadata.delay(str(asset.id), asset.file_path)
    return asset


@router.get("", response_model=AssetListResponse)
async def list_all(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    assets, total = await list_assets(db, skip=skip, limit=limit)
    return AssetListResponse(assets=assets, total=total)


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_one(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_asset(db, asset_id)


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    asset = await get_asset(db, asset_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, key, value)
    await db.flush()
    await db.refresh(asset)
    return asset


EXPORT_FORMATS = {
    "mp3": {"ffmpeg_fmt": "mp3", "mime": "audio/mpeg", "args": ["-ab", "192k", "-ac", "2", "-ar", "44100"]},
    "wav": {"ffmpeg_fmt": "wav", "mime": "audio/wav", "args": ["-ac", "2", "-ar", "44100"]},
    "flac": {"ffmpeg_fmt": "flac", "mime": "audio/flac", "args": ["-ac", "2", "-ar", "44100"]},
    "ogg": {"ffmpeg_fmt": "ogg", "mime": "audio/ogg", "args": ["-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5"]},
    "aac": {"ffmpeg_fmt": "adts", "mime": "audio/aac", "args": ["-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "192k"]},
}


@router.get("/{asset_id}/download")
async def download_asset(
    asset_id: uuid.UUID,
    format: str = Query("original", description="Export format: original, mp3, wav, flac, ogg, aac"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    import logging
    import subprocess
    asset = await get_asset(db, asset_id)
    file_path = asset.file_path
    safe_title = "".join(c for c in asset.title if c.isalnum() or c in " -_").strip() or "download"

    # Get the raw file data
    if file_path.startswith("http://") or file_path.startswith("https://"):
        if format == "original":
            return RedirectResponse(url=file_path)
        # Need to fetch the file for conversion
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(file_path, follow_redirects=True)
            data = resp.content
    else:
        from app.services.storage_service import download_file
        data = await download_file(file_path)

    # If original format requested, return as-is
    if format == "original":
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "mp3"
        return Response(
            content=data,
            media_type="audio/mpeg",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.{ext}"'},
        )

    # Convert to requested format via FFmpeg
    fmt_config = EXPORT_FORMATS.get(format)
    if not fmt_config:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}. Use: {', '.join(EXPORT_FORMATS.keys())}")

    try:
        result = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-f", fmt_config["ffmpeg_fmt"]] + fmt_config["args"] + ["pipe:1"],
            input=data,
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            logging.getLogger(__name__).warning("FFmpeg conversion failed: %s", result.stderr[:500])
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail="Audio conversion failed")
        converted = result.stdout
    except FileNotFoundError:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="FFmpeg not available on server")

    return Response(
        content=converted,
        media_type=fmt_config["mime"],
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.{format}"'},
    )


@router.post("/{asset_id}/transcode", response_model=TaskStatusResponse)
async def transcode(
    asset_id: uuid.UUID,
    body: TranscodeRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    asset = await get_asset(db, asset_id)
    result = task_transcode_audio.delay(str(asset.id), asset.file_path, body.codec, body.bitrate)
    return TaskStatusResponse(task_id=result.id, status="queued")


@router.post("/{asset_id}/clip", response_model=TaskStatusResponse)
async def clip(
    asset_id: uuid.UUID,
    body: ClipRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    asset = await get_asset(db, asset_id)
    result = task_clip_audio.delay(str(asset.id), asset.file_path, body.start, body.duration)
    return TaskStatusResponse(task_id=result.id, status="queued")


@router.delete("/{asset_id}", status_code=204)
async def delete(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await delete_asset(db, asset_id)
