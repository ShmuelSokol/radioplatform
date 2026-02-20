"""Show archive + podcast RSS feed API."""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, require_manager
from app.core.exceptions import NotFoundError
from app.models.show_archive import ShowArchive
from app.models.station import Station
from app.models.user import User
from app.schemas.show_archive import (
    ShowArchiveCreate, ShowArchiveUpdate, ShowArchiveInDB, ShowArchiveListResponse,
)

router = APIRouter(prefix="/archives", tags=["archives"])

# --- Admin CRUD ---


@router.post("", response_model=ShowArchiveInDB, status_code=201)
async def create_archive(
    body: ShowArchiveCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    archive = ShowArchive(**body.model_dump())
    db.add(archive)
    await db.flush()
    await db.refresh(archive)
    return archive


@router.get("", response_model=ShowArchiveListResponse)
async def list_archives(
    station_id: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Public: list published archives. Admin sees all."""
    q = select(ShowArchive).where(ShowArchive.is_published == True)
    count_q = select(func.count(ShowArchive.id)).where(ShowArchive.is_published == True)
    if station_id:
        q = q.where(ShowArchive.station_id == station_id)
        count_q = count_q.where(ShowArchive.station_id == station_id)
    q = q.order_by(ShowArchive.recorded_at.desc().nullslast(), ShowArchive.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(q)
    archives = result.scalars().all()
    total = (await db.execute(count_q)).scalar() or 0
    return ShowArchiveListResponse(archives=archives, total=total)


@router.get("/{archive_id}", response_model=ShowArchiveInDB)
async def get_archive(archive_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ShowArchive).where(ShowArchive.id == archive_id))
    archive = result.scalar_one_or_none()
    if not archive:
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
    for k, v in body.model_dump(exclude_unset=True).items():
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
    await db.delete(archive)


# --- Podcast RSS Feed ---


@router.get("/station/{station_id}/rss")
async def podcast_rss(station_id: str, db: AsyncSession = Depends(get_db)):
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
      <enclosure url="{escape(a.audio_url)}" type="audio/mpeg" />
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">{a.id}</guid>
      {duration}
      {f'<itunes:author>{escape(a.host_name)}</itunes:author>' if a.host_name else ''}
      {f'<itunes:image href="{escape(a.cover_image_url)}" />' if a.cover_image_url else ''}
    </item>"""

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(station.name)}</title>
    <description>{escape(station.description or f'{station.name} Show Archives')}</description>
    <language>en</language>
    <itunes:author>{escape(station.name)}</itunes:author>
    <itunes:category text="Music" />
    {f'<itunes:image href="{escape(station.logo_url)}" />' if station.logo_url else ''}
    {items_xml}
  </channel>
</rss>"""

    return Response(content=rss, media_type="application/rss+xml")
