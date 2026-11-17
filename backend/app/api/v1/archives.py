"""Show archive + podcast RSS feed API."""
import uuid
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_user, get_db, may_operate_station, require_manager
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.models.show_archive import ShowArchive
from app.models.station import Station
from app.models.user import User
from app.schemas.show_archive import (
    ShowArchiveCreate, ShowArchiveUpdate, ShowArchiveInDB, ShowArchiveListResponse,
)

router = APIRouter(prefix="/archives", tags=["archives"])


async def _sees_unpublished(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db: AsyncSession = Depends(get_db),
) -> bool:
    """Listeners see published archives only; a manager (the Archives admin
    screen) also sees drafts. A missing or bad login is simply a listener."""
    if credentials is None:
        return False
    try:
        user = await get_current_user(request, credentials, db)
    except UnauthorizedError:
        return False
    return user.role.value in ("admin", "manager")


async def _check_station(db: AsyncSession, user: User, station_id) -> uuid.UUID:
    """The archive's station must exist and the login must be allowed on it."""
    try:
        key = uuid.UUID(str(station_id))
    except ValueError:
        raise NotFoundError("Station not found")
    station = await db.get(Station, key)
    if station is None:
        raise NotFoundError("Station not found")
    if not may_operate_station(user, station):
        raise ForbiddenError("Your login is not set up to operate this station")
    return key

# --- Admin CRUD ---


@router.post("", response_model=ShowArchiveInDB, status_code=201)
async def create_archive(
    body: ShowArchiveCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    values = body.model_dump()
    values["station_id"] = await _check_station(db, _user, body.station_id)
    archive = ShowArchive(**values)
    db.add(archive)
    await db.flush()
    await db.refresh(archive)
    return archive


@router.get("", response_model=ShowArchiveListResponse)
async def list_archives(
    station_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    drafts: bool = Query(False, description="Include unpublished archives (admins and managers only)"),
    db: AsyncSession = Depends(get_db),
    everything: bool = Depends(_sees_unpublished),
):
    """Public: list published archives. With drafts=true an admin or manager also gets the
    unpublished ones (the admin page asks for them; the public page never shows drafts,
    even to a signed-in manager)."""
    q = select(ShowArchive)
    count_q = select(func.count(ShowArchive.id))
    if not (drafts and everything):
        q = q.where(ShowArchive.is_published == True)
        count_q = count_q.where(ShowArchive.is_published == True)
    if station_id:
        q = q.where(ShowArchive.station_id == station_id)
        count_q = count_q.where(ShowArchive.station_id == station_id)
    q = q.order_by(ShowArchive.recorded_at.desc().nullslast(), ShowArchive.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(q)
    archives = result.scalars().all()
    total = (await db.execute(count_q)).scalar() or 0
    return ShowArchiveListResponse(archives=archives, total=total)


@router.get("/{archive_id}", response_model=ShowArchiveInDB)
async def get_archive(
    archive_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    everything: bool = Depends(_sees_unpublished),
):
    result = await db.execute(select(ShowArchive).where(ShowArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive or (not archive.is_published and not everything):
        raise NotFoundError("Archive not found")
    return archive


@router.patch("/{archive_id}", response_model=ShowArchiveInDB)
async def update_archive(
    archive_id: uuid.UUID,
    body: ShowArchiveUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(select(ShowArchive).where(ShowArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise NotFoundError("Archive not found")
    await _check_station(db, _user, archive.station_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(archive, k, v)
    await db.flush()
    await db.refresh(archive)
    return archive


@router.delete("/{archive_id}", status_code=204)
async def delete_archive(
    archive_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    result = await db.execute(select(ShowArchive).where(ShowArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
        raise NotFoundError("Archive not found")
    await _check_station(db, _user, archive.station_id)
    await db.delete(archive)


# --- Podcast RSS Feed ---


@router.get("/station/{station_id}/rss")
async def podcast_rss(station_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """Public: RSS feed for a station's show archives (podcast format)."""
    # Get station info
    station_result = await db.execute(select(Station).where(Station.id == station_id))
    station = station_result.scalar_one_or_none()
    if not station:
        raise NotFoundError("Station not found")

    # Get published archives
    result = await db.execute(
        select(ShowArchive).where(
            ShowArchive.station_id == station_id,
            ShowArchive.is_published == True,
        ).order_by(ShowArchive.recorded_at.desc().nullslast()).limit(100)
    )
    archives = result.scalars().all()

    # Build RSS XML
    from xml.sax.saxutils import escape

    def attr(value: str) -> str:
        """Escaped for a double-quoted XML attribute (a quote in a URL must not end it)."""
        return escape(value, {'"': "&quot;"})

    items_xml = ""
    for a in archives:
        pub_date = a.recorded_at.strftime("%a, %d %b %Y %H:%M:%S +0000") if a.recorded_at else a.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
        duration = ""
        if a.duration_seconds:
            h = a.duration_seconds // 3600
            m = (a.duration_seconds % 3600) // 60
            s = a.duration_seconds % 60
            duration = f"<itunes:duration>{h:02d}:{m:02d}:{s:02d}</itunes:duration>"

        items_xml += f"""
    <item>
      <title>{escape(a.title)}</title>
      <description>{escape(a.description or '')}</description>
      <enclosure url="{attr(a.audio_url)}" length="0" type="audio/mpeg" />
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{a.id}</guid>
      {duration}
      {f'<itunes:author>{escape(a.host_name)}</itunes:author>' if a.host_name else ''}
      {f'<itunes:image href="{attr(a.cover_image_url)}" />' if a.cover_image_url else ''}
    </item>"""

    # Directories (Apple, validators) need the channel's web page and the feed's own
    # address. The file size is not stored, and RSS says length="0" when unknown.
    from app.config import settings

    site = f"{settings.FRONTEND_URL.rstrip('/')}/archives"
    if settings.BACKEND_PUBLIC_URL:
        self_url = f"{settings.BACKEND_PUBLIC_URL.rstrip('/')}{request.url.path}"
    else:
        self_url = str(request.url)

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(station.name)}</title>
    <link>{escape(site)}</link>
    <atom:link href="{attr(self_url)}" rel="self" type="application/rss+xml" />
    <description>{escape(station.description or f'{station.name} Show Archives')}</description>
    <language>en</language>
    <itunes:author>{escape(station.name)}</itunes:author>
    <itunes:category text="Music" />
    {f'<itunes:image href="{attr(station.logo_url)}" />' if station.logo_url else ''}
    {items_xml}
  </channel>
</rss>"""

    return Response(content=rss, media_type="application/rss+xml")
