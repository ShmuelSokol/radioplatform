"""What a local FM box needs to keep the station on air with no internet.

The box (a small computer beside the transmitter) runs the same playout engine as the
stream server, with a relay in front of it (`infra/local/kbr_relay.py`). While the
internet is up the relay passes the engine's calls on to this backend. It also fetches
this plan every minute and keeps the audio on its own disk, so when the line drops it
can answer the engine itself: the queue as it stood, timed items at their second, and
silence inside a blackout. See `wiki/local-fm-box.md`.

Read-only: nothing here changes the queue.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.asset import Asset, not_placeholder
from app.models.queue_entry import QueueEntry, is_soft_timed
from app.services import playout_engine

# Songs ahead that go in the plan. At about 4 minutes each this is well past two days;
# timed items are cut off by the hours asked for instead.
PLAN_MAX_ENTRIES = 1500
LIBRARY_PAGE = 500


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


async def build_plan(db: AsyncSession, station_id, hours: float = 48.0) -> dict:
    """The pending queue in play order with everything the engine needs for each item,
    and the blackout windows that touch the period."""
    from app.api.v1.queue import OPERATOR_SOURCES
    from app.services.spot_schedule_service import load_blackouts

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=hours)
    # Same rule as /next: a placeholder or do-not-play item queued automatically never airs
    may_air = or_(
        QueueEntry.source.in_(OPERATOR_SOURCES),
        QueueEntry.asset_id.notin_(select(Asset.id).where(~not_placeholder())),
    )
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            or_(QueueEntry.preempt_at.is_(None), QueueEntry.preempt_at <= horizon),
            may_air,
        )
        .order_by(QueueEntry.position)
        .limit(PLAN_MAX_ENTRIES)
    )
    entries = []
    for entry in res.unique().scalars().all():
        asset = entry.asset
        url = playout_engine.audio_url(asset)
        if not url:
            continue
        timed = entry.preempt_at is not None
        hard = timed and not is_soft_timed(entry)
        entries.append({
            "entry_id": str(entry.id),
            "position": entry.position,
            "at": _epoch(entry.preempt_at),
            "mode": ("hard" if hard else "soft") if timed else None,
            "hard_by": _epoch(entry.hard_by),
            "kind": (asset.asset_type or "").lower(),
            "title": asset.title or "",
            "duration": asset.duration,
            "url": url,
            # The same strings /next and the timed hand-over would give the engine
            "uri_main": playout_engine.build_uri(entry, "main"),
            "uri_timed": playout_engine.build_uri(entry, "timed") if hard else None,
        })
    blackouts = await load_blackouts(db, station_id, now - timedelta(hours=1), horizon)
    return {
        "station_id": str(station_id),
        "generated_at": now.timestamp(),
        "hours": hours,
        "entries": entries,
        "blackouts": [
            {"start": b.start.timestamp(), "end": b.end.timestamp(), "name": b.name}
            for b in blackouts
        ],
    }


async def library_page(db: AsyncSession, after: str | None = None, limit: int = LIBRARY_PAGE) -> dict:
    """One page of the whole library's audio addresses, by id, for the box's full copy.
    `next` is the `after` of the following page, or None on the last one."""
    limit = max(1, min(int(limit), 2000))
    stmt = select(Asset.id, Asset.file_path, Asset.asset_type).order_by(Asset.id).limit(limit)
    if after:
        import uuid

        stmt = stmt.where(Asset.id > uuid.UUID(after))
    rows = (await db.execute(stmt)).all()
    items = []
    for asset_id, file_path, asset_type in rows:
        url = playout_engine.audio_url(_PathOnly(file_path))
        if url:
            items.append({"asset_id": str(asset_id), "url": url, "kind": (asset_type or "").lower()})
    return {"items": items, "next": str(rows[-1][0]) if len(rows) == limit else None}


class _PathOnly:
    def __init__(self, file_path):
        self.file_path = file_path
