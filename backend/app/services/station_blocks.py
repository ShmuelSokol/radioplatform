"""Items one station never plays by itself ("Not on this station" in the control room).

The library is shared by all stations, so category do_not_play takes an item off every
station. A row in station_blocked_assets takes it off one station only. The rule is the
same as for do_not_play on that station: no automatic pick draws it, a copy that reached
the queue some other way is skipped when its turn comes, and what an operator puts in
the queue by hand still plays. See wiki/control-room.md.
"""
import uuid

from sqlalchemy import delete, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.station_blocked_asset import StationBlockedAsset


def _uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None  # not an id any station has


def blocked_asset_ids(station_id):
    """Subquery of the asset ids this station does not play; use as
    `Asset.id.notin_(blocked_asset_ids(station_id))`."""
    sid = _uuid(station_id)
    query = select(StationBlockedAsset.asset_id)
    return query.where(false()) if sid is None else query.where(StationBlockedAsset.station_id == sid)


async def is_blocked(db: AsyncSession, station_id, asset_id) -> bool:
    row = await db.execute(
        select(StationBlockedAsset.asset_id).where(
            StationBlockedAsset.station_id == _uuid(station_id),
            StationBlockedAsset.asset_id == _uuid(asset_id),
        )
    )
    return row.first() is not None


async def set_blocked(db: AsyncSession, station_id, asset_id, on: bool) -> dict:
    """Keep an item off one station (or allow it there again).

    On: the item is listed for the station and its waiting copies in that station's
    queue are removed, the same way "do not play" removes them everywhere (never the
    copy on air, one the playout engine holds, or a timed one). Other stations, the
    item's category and everything else about it are untouched. Off: the listing goes;
    nothing is queued again, the next automatic draw may pick it.
    """
    from app.services.asset_service import drop_waiting_copies, get_asset
    from app.services.station_service import get_station

    station = await get_station(db, _uuid(station_id))
    asset = await get_asset(db, _uuid(asset_id))
    removed = kept = 0
    if on:
        if not await is_blocked(db, station.id, asset.id):
            db.add(StationBlockedAsset(station_id=station.id, asset_id=asset.id))
            await db.flush()
        removed, kept = await drop_waiting_copies(db, asset.id, station.id)
    else:
        await db.execute(
            delete(StationBlockedAsset)
            .where(StationBlockedAsset.station_id == station.id, StationBlockedAsset.asset_id == asset.id)
            .execution_options(synchronize_session=False)
        )
    await db.flush()
    return {
        "id": str(asset.id),
        "station_id": str(station.id),
        "station_name": station.name,
        "blocked": on,
        "removed_from_queue": removed,
        "still_to_play": kept,
    }
