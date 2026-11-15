"""Admin-only backup downloads."""
import datetime as dt

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.dependencies import require_admin
from app.db.engine import engine
from app.models.user import User
from app.services import backup_service

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/database")
async def download_database_backup(_admin: User = Depends(require_admin)):
    """The whole database as one .json.gz file, with a manifest of audio files."""
    async with engine.connect() as conn:
        backup = await backup_service.export_database(conn)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=backup_service.pack(backup),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="kbr-backup-{stamp}.json.gz"'},
    )


@router.get("/files")
async def list_backup_files(_admin: User = Depends(require_admin)):
    """Every audio and image file the database points at, with its download URL."""
    async with engine.connect() as conn:
        backup = await backup_service.export_database(conn)
    return {"created_at": backup["created_at"], "row_counts": backup["row_counts"], "files": backup["files"]}
