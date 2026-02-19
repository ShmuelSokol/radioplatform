import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    file_data = await file.read()
    asset = await create_asset(
        db,
        title=title,
        filename=file.filename or "upload.mp3",
        file_data=file_data,
        content_type=file.content_type or "audio/mpeg",
        user_id=user.id,
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
