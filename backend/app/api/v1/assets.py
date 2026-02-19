import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
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
    format: str = Form("mp2"),
    artist: str | None = Form(None),
    album: str | None = Form(None),
    asset_type: str = Form("music"),
    category: str | None = Form(None),
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
        artist=artist or None,
        album=album or None,
        asset_type=asset_type,
        category=category or None,
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


@router.get("/{asset_id}/audio-url")
async def get_audio_url(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return a public URL for wavesurfer.js to fetch audio directly."""
    asset = await get_asset(db, asset_id)
    file_path = asset.file_path
    if file_path.startswith("http://") or file_path.startswith("https://"):
        return {"url": file_path}
    # Build Supabase public URL if configured
    from app.config import settings
    if settings.supabase_storage_enabled:
        bucket = settings.SUPABASE_STORAGE_BUCKET
        url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_path}"
        return {"url": url}
    # Fallback: proxy through download endpoint
    return {"url": f"/api/v1/assets/{asset_id}/download"}


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
    "mp2": {"ffmpeg_fmt": "mp2", "mime": "audio/mpeg", "args": ["-vn", "-c:a", "mp2", "-b:a", "192k", "-ac", "2", "-ar", "44100"]},
    "mp3": {"ffmpeg_fmt": "mp3", "mime": "audio/mpeg", "args": ["-vn", "-ab", "192k", "-ac", "2", "-ar", "44100"]},
    "mp4": {"ffmpeg_fmt": "mp4", "mime": "audio/mp4", "args": ["-vn", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100", "-movflags", "+faststart"]},
    "wav": {"ffmpeg_fmt": "wav", "mime": "audio/wav", "args": ["-vn", "-ac", "2", "-ar", "44100"]},
    "flac": {"ffmpeg_fmt": "flac", "mime": "audio/flac", "args": ["-vn", "-ac", "2", "-ar", "44100"]},
    "ogg": {"ffmpeg_fmt": "ogg", "mime": "audio/ogg", "args": ["-vn", "-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "5"]},
    "aac": {"ffmpeg_fmt": "adts", "mime": "audio/aac", "args": ["-vn", "-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "192k"]},
}


@router.get("/{asset_id}/download")
async def download_asset(
    asset_id: uuid.UUID,
    format: str = Query("original", description="Export format: original, mp3, wav, flac, ogg, aac"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from fastapi import HTTPException
    asset = await get_asset(db, asset_id)
    file_path = asset.file_path
    safe_title = "".join(c for c in asset.title if c.isalnum() or c in " -_").strip() or "download"

    # Always fetch the raw bytes server-side.
    # Never redirect to Supabase — the frontend Axios client forwards the JWT
    # Authorization header on redirects, which triggers a CORS preflight that
    # Supabase rejects.
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(file_path, follow_redirects=True)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="Failed to fetch file from storage")
            data = resp.content
    else:
        from app.services.storage_service import download_file
        data = await download_file(file_path)

    # Return original format as-is
    if format == "original":
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "mp3"
        return Response(
            content=data,
            media_type="audio/mpeg",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.{ext}"'},
        )

    # Convert using the shared service (includes temp-file fallback for MPEG etc.)
    fmt_config = EXPORT_FORMATS.get(format)
    if not fmt_config:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}. Use: {', '.join(EXPORT_FORMATS.keys())}")

    from app.services.audio_convert_service import convert_audio
    input_ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ".mp3"
    converted, _duration, _ext = convert_audio(data, f"download{input_ext}", format)

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


@router.post("/{asset_id}/detect-silence")
async def detect_silence_endpoint(
    asset_id: uuid.UUID,
    threshold_db: float = Query(-30),
    min_duration: float = Query(0.5),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Detect silence regions in an asset's audio file."""
    asset = await get_asset(db, asset_id)
    file_path = asset.file_path

    # Get file data
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(file_path, follow_redirects=True)
            data = resp.content
    else:
        from app.services.storage_service import download_file
        data = await download_file(file_path)

    from app.services.silence_service import detect_silence
    regions = detect_silence(data, threshold_db=threshold_db, min_duration=min_duration)

    # Store in metadata_extra
    extra = dict(asset.metadata_extra or {})
    extra["silence_regions"] = regions
    asset.metadata_extra = extra
    await db.flush()
    await db.refresh(asset)

    return {"silence_regions": regions}


@router.post("/{asset_id}/trim", response_model=AssetResponse)
async def trim_asset_endpoint(
    asset_id: uuid.UUID,
    trim_start: float = Query(...),
    trim_end: float = Query(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Trim audio to [trim_start, trim_end]. Non-destructive: keeps original."""
    asset = await get_asset(db, asset_id)
    file_path = asset.file_path

    # Get file data
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(file_path, follow_redirects=True)
            data = resp.content
    else:
        from app.services.storage_service import download_file
        data = await download_file(file_path)

    from app.services.silence_service import trim_audio
    trimmed_data, new_duration = trim_audio(data, trim_start, trim_end)

    # Upload trimmed file
    from app.services.storage_service import generate_asset_key, upload_file as upload_storage
    new_key = generate_asset_key("trimmed.mp3")
    await upload_storage(trimmed_data, new_key, "audio/mpeg")

    # Update asset non-destructively
    extra = dict(asset.metadata_extra or {})
    if "original_file_path" not in extra:
        extra["original_file_path"] = asset.file_path
    trim_entry = {"from": asset.file_path, "to": new_key, "trim_start": trim_start, "trim_end": trim_end}
    extra.setdefault("trim_history", []).append(trim_entry)
    extra.pop("silence_regions", None)

    asset.file_path = new_key
    asset.duration = new_duration
    asset.metadata_extra = extra
    await db.flush()
    await db.refresh(asset)

    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    await delete_asset(db, asset_id)
