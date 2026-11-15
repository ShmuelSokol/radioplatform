"""Plain-text now-playing feed for an FM RDS encoder (for example Magic RDS).

RDS software polls a URL or a text file and sends the line it finds to the
transmitter as RadioText. This endpoint is that line: public, plain text, one
line, no JSON, trimmed to the RDS RadioText limit.
"""
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station

router = APIRouter(prefix="/stations", tags=["rds"])

RADIOTEXT_MAX = 64
# Items that are station furniture, not something to name on the radio display.
_UNNAMED_TYPES = {"jingle", "station_id", "time_announcement", "weather", "weather_spot", "silence", "blank"}


def _rds_safe(text: str) -> str:
    """One line, no control characters, plain punctuation the RDS charset has."""
    swaps = {"—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."}
    for bad, good in swaps.items():
        text = text.replace(bad, good)
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())


def radiotext(asset: Asset | None, station_name: str, limit: int = RADIOTEXT_MAX) -> str:
    """The line to show: "Artist - Title" for music and programs, the sponsor's
    name for a sponsorship, and the station's name for jingles and IDs."""
    line = station_name
    if asset is not None:
        kind = (asset.asset_type or "").lower()
        category = (asset.category or "").lower()
        if kind not in _UNNAMED_TYPES and category not in _UNNAMED_TYPES:
            title = (asset.title or "").strip()
            artist = (asset.artist or "").strip()
            if title:
                line = f"{artist} - {title}" if artist and kind != "spot" else title
    return _rds_safe(line)[:limit].rstrip()


@router.get("/{station_id}/rds.txt", response_class=PlainTextResponse)
async def rds_text(
    station_id: uuid.UUID,
    max: int = Query(RADIOTEXT_MAX, ge=8, le=128, description="Longest line to return"),
    db: AsyncSession = Depends(get_db),
):
    station = (await db.execute(select(Station).where(Station.id == station_id))).scalar_one_or_none()
    if station is None:
        return PlainTextResponse("", status_code=404)
    asset = (
        await db.execute(
            select(Asset)
            .join(QueueEntry, QueueEntry.asset_id == Asset.id)
            .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
            .order_by(QueueEntry.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return PlainTextResponse(
        radiotext(asset, station.name, max),
        headers={"Cache-Control": "no-store"},
    )
