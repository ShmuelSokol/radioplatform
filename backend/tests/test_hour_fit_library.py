"""The song before a hard break taken from the block's playlist when no order of the
waiting songs ends near it.

Live 2026-11-25 on Main: the Night instrumental block (3-6 AM, tracks of 3 to 12
minutes) ends at the 6 AM break, and after the run-up the queue held only songs of the
6 AM block. Reordering had nothing to choose from: the Gerstner Medley (658 s) started
at 5:50:21 and lost 70 s. A 578 s track of the same block would have ended on the hour.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule_block import ScheduleBlock
from tests.test_carlebach_half_hour import _blocks
from tests.test_hour_fit import HOUR, _lost_at_the_hour, _order, _queue, _station, fit_before_hard_item

ENDS = HOUR - timedelta(minutes=10)  # the medley would start at :50 and lose 4 minutes


async def _night(db: AsyncSession, library: list[tuple[str, float]]):
    station = await _station(db)
    sid = station.id
    ids = await _blocks(db, station, {"night": (-3, 0), "morning": (0, 6)})
    template = PlaylistTemplate(id=uuid.uuid4(), name="Main - Night instrumental", station_id=sid,
                                is_active=True)
    db.add(template)
    await db.flush()
    db.add(TemplateSlot(id=uuid.uuid4(), template_id=template.id, position=0, asset_type="music",
                        category="instrumental"))
    await db.execute(update(ScheduleBlock).where(ScheduleBlock.id == uuid.UUID(ids["night"]))
                     .values(playlist_template_id=template.id))
    assets = {}
    for title, seconds in library:
        a = Asset(id=uuid.uuid4(), title=title, artist=f"{title} artist", duration=seconds,
                  file_path=f"{uuid.uuid4().hex}.mp3", asset_type="music", category="instrumental")
        db.add(a)
        assets[title] = a
    await db.flush()
    await _queue(db, station, ENDS, [
        ("Medley", 840.0, {"block_id": ids["night"]}),
        ("Morning 1", 240.0, {"block_id": ids["morning"]}),
        ("Morning 2", 240.0, {"block_id": ids["morning"]}),
    ])
    return sid, {t: a.id for t, a in assets.items()}


@pytest.mark.asyncio
async def test_a_song_of_the_block_that_ends_on_the_hour_takes_the_last_place(db_session):
    sid, _ = await _night(db_session, [("Short", 300.0), ("Ten minutes", 611.0), ("Long", 720.0)])
    title, _, lost = await _lost_at_the_hour(db_session, sid, ENDS)
    assert (title, round(lost)) == ("Medley", 240)
    assert await fit_before_hard_item(db_session, sid, HOUR - timedelta(minutes=25))
    await db_session.commit()
    assert await _order(db_session, sid) == ["Ten minutes", "Morning 1", "Morning 2"]
    title, _, lost = await _lost_at_the_hour(db_session, sid, ENDS)
    assert title == "Ten minutes" and lost <= 15


@pytest.mark.asyncio
async def test_a_song_that_aired_lately_or_waits_in_the_queue_is_not_used(db_session):
    sid, assets = await _night(db_session, [("Ten minutes", 611.0), ("Also ten", 612.0)])
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=sid, asset_id=assets["Ten minutes"],
                           start_utc=HOUR - timedelta(hours=2), end_utc=HOUR - timedelta(hours=2) + timedelta(minutes=10)))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=sid, asset_id=assets["Also ten"],
                              position=50, status="pending", source="auto"))
    await db_session.commit()
    assert not await fit_before_hard_item(db_session, sid, HOUR - timedelta(minutes=25))
    assert (await _order(db_session, sid))[:3] == ["Medley", "Morning 1", "Morning 2"]


@pytest.mark.asyncio
async def test_nothing_comes_from_the_library_when_a_reorder_fits(db_session):
    """The waiting songs come first: only when none of their orders ends near the hour."""
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"night": (-3, 0), "morning": (0, 6)})
    db_session.add(Asset(id=uuid.uuid4(), title="Ten minutes", artist="x", duration=611.0,
                         file_path="x.mp3", asset_type="music", category="Music"))
    await _queue(db_session, station, ENDS, [
        ("Medley", 840.0, {"block_id": ids["night"]}),
        ("Fits", 611.0, {"block_id": ids["night"]}),
        ("Morning 1", 240.0, {"block_id": ids["morning"]}),
    ])
    assert await fit_before_hard_item(db_session, sid, HOUR - timedelta(minutes=25))
    await db_session.commit()
    assert (await _order(db_session, sid))[0] == "Fits"


@pytest.mark.asyncio
async def test_the_redraw_before_a_block_change_on_the_hour_keeps_the_fitted_songs(db_session):
    """Main 2026-11-25: the redraw 15 minutes before the 6 AM block change kept one song
    and drew the rest again, throwing away the songs fitted to end at 6:00; the new
    last song lost 70 s. Now the songs that start before a hard item at the change stay."""
    from app.services.schedule_refresh_service import _songs_before_hard_change

    station = await _station(db_session)
    sid = station.id
    await _queue(db_session, station, HOUR - timedelta(minutes=10), [
        ("Night 1", 300.0), ("Night 2", 303.0), ("After 1", 240.0), ("After 2", 240.0),
    ])
    station = await db_session.get(type(station), sid)
    assert await _songs_before_hard_change(db_session, station, HOUR - timedelta(minutes=14), HOUR) == 2
    # no hard item at the change: the old rule (KEEP_NEXT)
    assert await _songs_before_hard_change(db_session, station, HOUR - timedelta(minutes=14),
                                           HOUR + timedelta(minutes=10)) is None
