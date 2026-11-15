"""
Sponsorship spot recordings: write the script, check the wording, make the
audio, listen, approve. An approved recording becomes a library asset tied to
the sponsor, ready for the Spot Schedule. See wiki/spot-recordings.md.
"""

import logging
import re
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.core.exceptions import NotFoundError
from app.models.asset import Asset
from app.models.sponsor import Sponsor
from app.models.spot_recording import SpotRecording
from app.models.user import User
from app.schemas.spot_recording import (
    SpotRecordingCreate,
    SpotRecordingOut,
    SpotRecordingUpdate,
)
from app.services.copy_review_service import review_copy
from app.services.spot_voice_service import generate_spot_audio
from app.services.storage_service import delete_file, download_file, upload_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spot-recordings", tags=["spot-recordings"])

MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _out(rec: SpotRecording) -> SpotRecordingOut:
    out = SpotRecordingOut.model_validate(rec)
    out.has_audio = bool(rec.audio_key)
    return out


async def _get(db: AsyncSession, recording_id: UUID) -> SpotRecording:
    rec = (
        await db.execute(select(SpotRecording).where(SpotRecording.id == recording_id))
    ).scalar_one_or_none()
    if not rec:
        raise NotFoundError("Spot recording not found")
    return rec


async def _sponsor_name(db: AsyncSession, sponsor_id) -> str | None:
    return (
        await db.execute(select(Sponsor.name).where(Sponsor.id == sponsor_id))
    ).scalar_one_or_none()


async def _drop_audio(rec: SpotRecording) -> None:
    if rec.audio_key:
        try:
            await delete_file(rec.audio_key)
        except Exception:
            logger.warning("Could not delete old spot audio %s", rec.audio_key)
    rec.audio_key = None
    rec.duration = None
    rec.voice = None


def _editable(rec: SpotRecording) -> None:
    if rec.status == "approved":
        raise HTTPException(
            status_code=409, detail="This spot is approved and in the library. Start a new one."
        )


async def _store(rec: SpotRecording, data: bytes, ext: str) -> None:
    key = f"spots/recordings/{rec.id}-{uuid.uuid4().hex[:8]}{ext}"
    await upload_file(data, key, MIME.get(ext, "application/octet-stream"))
    await _drop_audio(rec)
    rec.audio_key = key


@router.get("", response_model=list[SpotRecordingOut])
async def list_spot_recordings(
    sponsor_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    stmt = select(SpotRecording).order_by(SpotRecording.created_at.desc())
    if sponsor_id:
        stmt = stmt.where(SpotRecording.sponsor_id == sponsor_id)
    return [_out(r) for r in (await db.execute(stmt)).scalars().all()]


@router.post("", response_model=SpotRecordingOut, status_code=201)
async def create_spot_recording(
    data: SpotRecordingCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    name = await _sponsor_name(db, data.sponsor_id)
    if name is None:
        raise NotFoundError("Sponsor not found")
    rec = SpotRecording(
        sponsor_id=data.sponsor_id,
        title=data.title.strip(),
        script_text=data.script_text.strip(),
        status="draft",
        review=await review_copy(db, data.script_text, name),
        created_by=user.id,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return _out(rec)


@router.put("/{recording_id}", response_model=SpotRecordingOut)
async def update_spot_recording(
    recording_id: UUID,
    data: SpotRecordingUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    rec = await _get(db, recording_id)
    _editable(rec)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("title"):
        rec.title = changes["title"].strip()
    if "notes" in changes:
        rec.notes = changes["notes"]
    new_script = (changes.get("script_text") or "").strip()
    if new_script and new_script != rec.script_text:
        # New words: the old audio no longer matches, and the check must be redone.
        rec.script_text = new_script
        rec.review = await review_copy(db, new_script, await _sponsor_name(db, rec.sponsor_id))
        await _drop_audio(rec)
        rec.status = "draft"
    await db.commit()
    await db.refresh(rec)
    return _out(rec)


@router.post("/{recording_id}/generate", response_model=SpotRecordingOut)
async def generate_audio(
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    """Make the audio from the script (Shprach when connected, placeholder until then)."""
    rec = await _get(db, recording_id)
    _editable(rec)
    data, seconds, voice, ext = await generate_spot_audio(rec.script_text)
    await _store(rec, data, ext)
    rec.duration = seconds
    rec.voice = voice
    rec.status = "ready"
    await db.commit()
    await db.refresh(rec)
    return _out(rec)


@router.post("/{recording_id}/audio", response_model=SpotRecordingOut)
async def upload_audio(
    recording_id: UUID,
    file: UploadFile = File(...),
    source: str = Form("upload"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    """
    Attach audio made elsewhere. Same multipart shape as the weather and time
    uploads: `file` plus an optional `source` (the studio PC sends "shprach").
    """
    if not re.fullmatch(r"[a-z0-9-]{1,30}", source) or source == "placeholder":
        raise HTTPException(status_code=422, detail="source must be a short lowercase name")
    rec = await _get(db, recording_id)
    _editable(rec)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="The file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 25 MB")
    from app.services.audio_convert_service import convert_audio

    try:
        converted, seconds, ext = convert_audio(raw, file.filename or "spot.wav", "mp3")
    except Exception:
        logger.exception("Spot audio conversion failed")
        raise HTTPException(status_code=422, detail="That file could not be read as audio")
    await _store(rec, converted, ext)
    rec.duration = seconds
    rec.voice = source
    rec.status = "ready"
    await db.commit()
    await db.refresh(rec)
    return _out(rec)


@router.get("/{recording_id}/audio")
async def get_audio(
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    rec = await _get(db, recording_id)
    if not rec.audio_key:
        raise NotFoundError("No audio yet")
    ext = "." + rec.audio_key.rsplit(".", 1)[-1]
    return Response(
        content=await download_file(rec.audio_key),
        media_type=MIME.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/{recording_id}/approve", response_model=SpotRecordingOut)
async def approve_spot_recording(
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Put the spot in the library as this sponsor's audio so it can be scheduled."""
    rec = await _get(db, recording_id)
    if rec.status == "approved":
        return _out(rec)
    if not rec.audio_key:
        raise HTTPException(status_code=409, detail="Make or upload the audio first")
    if rec.voice == "placeholder":
        raise HTTPException(
            status_code=409,
            detail="This is placeholder audio. Upload the real recording before approving.",
        )
    asset = Asset(
        title=rec.title,
        file_path=rec.audio_key,
        duration=rec.duration,
        created_by=user.id,
        asset_type="spot",
        category="sponsorship",
        review_status="approved",
        sponsor_id=rec.sponsor_id,
        metadata_extra={"spot_recording_id": str(rec.id), "script": rec.script_text},
    )
    db.add(asset)
    await db.flush()
    rec.asset_id = asset.id
    rec.status = "approved"
    await db.commit()
    await db.refresh(rec)
    return _out(rec)


@router.post("/{recording_id}/reject", response_model=SpotRecordingOut)
async def reject_spot_recording(
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    rec = await _get(db, recording_id)
    _editable(rec)
    rec.status = "rejected"
    await db.commit()
    await db.refresh(rec)
    return _out(rec)


@router.delete("/{recording_id}", status_code=204)
async def delete_spot_recording(
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    rec = await _get(db, recording_id)
    if rec.status != "approved":  # approved audio now belongs to the library asset
        await _drop_audio(rec)
    await db.delete(rec)
    await db.commit()
