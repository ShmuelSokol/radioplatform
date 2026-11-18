"""Keep a station's queue positions compact (1, 2, 3 ...) in one statement.

Every song change renumbers the queue. Loading each row and setting its position
sent one UPDATE per row: about 690 on Main with a two-day queue, over 3 s before
the next item was announced (2026-11-17). One statement does the same in a few
milliseconds. Used by the browser player's advance, the engine's start report and
the blackout fill, so the three cannot drift apart again.
"""
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.queue_entry import QueueEntry


async def compact_positions(db: AsyncSession, station_id, *, playing_first: bool = True) -> None:
    """Renumber the playing and pending entries from 1, keeping their order.

    `playing_first` puts the item on air at position 1 whatever its old position.
    Rows already in place are not written. Objects loaded in this session keep the
    position they were read with; read the row again if the new one is needed.
    """
    await db.flush()
    order = (QueueEntry.position, QueueEntry.created_at, QueueEntry.id)
    if playing_first:
        order = (QueueEntry.status.desc(), *order)  # "playing" sorts after "pending"
    ranked = (
        select(
            QueueEntry.id.label("id"),
            func.row_number().over(order_by=order).label("rn"),
        )
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .subquery()
    )
    await db.execute(
        update(QueueEntry)
        .where(QueueEntry.id == ranked.c.id, QueueEntry.position != ranked.c.rn)
        .values(position=ranked.c.rn)
        .execution_options(synchronize_session=False)
    )


async def put_playing_first(db: AsyncSession, station_id, entry) -> None:
    """Give the item that just went on air the place just before the first waiting one.

    What compact_positions does for the item on air, without rewriting every waiting
    row. With a 48 h queue that renumber rewrote some 900 rows on every song change:
    0.9 to 1.1 s of each engine start report on production (2026-11-18 12:07Z to
    12:24Z). The waiting rows keep their positions, so they stay in order; numbers
    simply move up by one per item played. Readers only compare positions (order by,
    max + 1, insert before or after a row), none expects the queue to start at 1.
    """
    first_waiting = (await db.execute(
        select(func.min(QueueEntry.position)).where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.id != entry.id,
        )
    )).scalar()
    if first_waiting is not None and entry.position != first_waiting - 1:
        entry.position = first_waiting - 1
