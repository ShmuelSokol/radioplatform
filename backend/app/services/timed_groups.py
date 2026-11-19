"""Hard timed groups never cut each other short (V1-10b).

A group is the hard timed items due at the same moment (time check, weather, a block's
items). When a second group is due while the first is still playing (a 6 s time check at
hh:00:00 and an announcement at hh:00:01), every player used to cut the first one: the
server, the page and the engine all start a hard item exactly on its time. Moving the
later group to the moment the earlier one ends, ahead of time, fixes all three at once:
the page pre-loads from the new time and the engine is handed the new time.

Not applied behind a shiur or story (long-form) item: the station's rule for long items
(pause, wait, cut) decides that. Silence (Shabbos / Yom Tov) is never moved and never
waited for. A move longer than MAX_SHIFT_SECONDS is not made: something is wrong with the
items' lengths and the clock time wins.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.asset import stored_analysis
from app.models.queue_entry import QueueEntry, hard_timed
from app.services import long_items

logger = logging.getLogger(__name__)

MAX_SHIFT_SECONDS = 120.0
DEFAULT_ITEM_SECONDS = 30.0


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _length(entry: QueueEntry) -> float:
    asset = entry.asset
    if asset is None:
        return DEFAULT_ITEM_SECONDS
    try:
        cue_out = stored_analysis(asset.metadata_extra).get("cue_out_seconds")
    except Exception:
        cue_out = None
    return float(cue_out or asset.duration or DEFAULT_ITEM_SECONDS)


def _is_silence(entry: QueueEntry) -> bool:
    return entry.asset is not None and (entry.asset.asset_type or "") == "silence"


async def space_hard_groups(
    db: AsyncSession, station_id, now: datetime, horizon: datetime, frozen: set[str] | None = None
) -> dict[str, datetime]:
    """Move hard groups due before an earlier short hard group has finished to the moment
    it ends. `frozen`: entry ids that may not move (already handed to the engine).

    Returns the moved entries, each with the time of the group it now follows on from
    (the first of a chain). The engine is handed them at that time, so it plays them as
    part of that group, after it, instead of taking over again and cutting it.
    Does not commit."""
    frozen = frozen or set()
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            hard_timed(),
            QueueEntry.preempt_at.isnot(None),
            QueueEntry.preempt_at <= horizon,
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )
    pending = [e for e in res.unique().scalars().all() if not _is_silence(e)]
    if not pending:
        return {}

    # Where the timeline is free again: after a hard timed item on air and the rest of its
    # group. Music, soft items and long-form items never hold a group back.
    free_at: datetime | None = None
    res = await db.execute(
        select(QueueEntry)
        .options(joinedload(QueueEntry.asset))
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .order_by(QueueEntry.started_at.desc().nullslast())
    )
    current = res.unique().scalars().first()
    current_group_at = None
    if (
        current is not None
        and current.preempt_at is not None
        and current.started_at is not None
        and current.timing_mode != "soft"
        and current.source != "ad_slot"
        and not long_items.is_long_form(current.asset)
        and not _is_silence(current)
    ):
        free_at = _aware(current.started_at) + timedelta(seconds=_length(current))
        current_group_at = _aware(current.preempt_at)

    groups: list[tuple[datetime, list[QueueEntry]]] = []
    for e in pending:
        at = _aware(e.preempt_at)
        if groups and groups[-1][0] == at:
            groups[-1][1].append(e)
        else:
            groups.append((at, [e]))

    moved: dict[str, datetime] = {}
    lead_at: datetime | None = current_group_at
    for at, items in groups:
        if current_group_at is not None and at == current_group_at:
            # The rest of the group on air: it follows on from it
            free_at = (free_at or at) + timedelta(seconds=sum(_length(e) for e in items))
            continue
        long_group = any(long_items.is_long_form(e.asset) for e in items)
        start = at
        if (
            free_at is not None
            and at < free_at
            and (free_at - at).total_seconds() <= MAX_SHIFT_SECONDS
            and not any(str(e.id) in frozen for e in items)
            # The song that ends at candle lighting never moves (shabbos_ending)
            and not any(e.source == "shabbos_end" for e in items)
        ):
            start = free_at
            leader = lead_at or at
            for e in items:
                e.preempt_at = start
                moved[str(e.id)] = leader
            logger.info(
                "Station %s: timed group due %s follows the one before it, now at %s",
                station_id, at.isoformat(), start.isoformat(),
            )
        if start == at:
            lead_at = at  # a group on its own time starts a new chain
        if long_group:
            # A shiur in a group: the long-item rule decides what comes after it
            free_at = None
            lead_at = None
        else:
            free_at = start + timedelta(seconds=sum(_length(e) for e in items))
    if moved:
        await db.flush()
    return moved
