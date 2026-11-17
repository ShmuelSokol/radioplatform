"""Listeners pre-load the item the queue will pick when the song ends.

Live check: a soft sponsorship came due during a song. The server waited for the
song to end and then started the sponsorship, but the now-playing message had
named the next song as "next" (picked by the time the song started), so the
listener's player crossfaded into that song, played 3 s of it, then jumped into
the sponsorship 3 s late.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1 import queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.services.scheduler_engine import SchedulerEngine
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


async def _with_soft_spot(db, elapsed, due_in):
    station, playing, pending = await _station_with_two_tracks(db, elapsed_seconds=elapsed)
    spot = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0,
                 file_path="https://cdn.example.com/spot.mp3", asset_type="spot")
    db.add(spot)
    await db.flush()
    slot = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=3,
                      status="pending", source="ad_slot", timing_mode="soft",
                      preempt_at=datetime.now(UTC) + timedelta(seconds=due_in))
    db.add(slot)
    await db.commit()
    return station, playing, pending, spot


async def _next_named(db, station, playing):
    await db.refresh(playing, ["asset"])
    payload = await SchedulerEngine().queue_entry_payload(db, station.id, playing)
    return payload["next_asset"]["id"]


@pytest.mark.asyncio
async def test_soft_slot_due_before_the_song_ends_is_next(db_session):
    # 180 s song, 10 s in; the sponsorship comes due 60 s from now (mid-song)
    station, playing, _, spot = await _with_soft_spot(db_session, elapsed=10, due_in=60)
    assert await _next_named(db_session, station, playing) == str(spot.id)


@pytest.mark.asyncio
async def test_soft_slot_due_after_the_song_waits(db_session):
    station, playing, pending, _ = await _with_soft_spot(db_session, elapsed=10, due_in=300)
    assert await _next_named(db_session, station, playing) == str(pending.asset_id)


@pytest.mark.asyncio
async def test_prediction_matches_what_the_queue_plays(db_session):
    # Just past the crossfade point (177 s of 180): the queue advances now
    station, playing, _, spot = await _with_soft_spot(db_session, elapsed=177.5, due_in=-30)
    predicted = await _next_named(db_session, station, playing)
    started = await queue_module._check_advance(db_session, station.id, 3.0)
    assert str(started.asset_id) == predicted == str(spot.id)


async def _next_payload(db, station, playing):
    await db.refresh(playing, ["asset"])
    payload = await SchedulerEngine().queue_entry_payload(db, station.id, playing)
    return payload["next_asset"]


@pytest.mark.asyncio
async def test_next_hard_item_carries_its_cut_time(db_session):
    # Pages start a hard item at its time themselves (they were 1.1-1.8 s late
    # waiting for this message at 13:00Z 2026-11-17)
    station, playing, _ = await _station_with_two_tracks(db_session, elapsed_seconds=10)
    clip = Asset(id=uuid.uuid4(), title="Time", duration=5.0,
                 file_path="https://cdn.example.com/t.mp3", asset_type="time")
    db_session.add(clip)
    await db_session.flush()
    due = datetime.now(UTC) + timedelta(seconds=60)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=3,
                              status="pending", source="block", preempt_at=due))
    await db_session.commit()
    nxt = await _next_payload(db_session, station, playing)
    assert nxt["id"] == str(clip.id)
    assert nxt["cut_at"] is not None
    assert abs(datetime.fromisoformat(nxt["cut_at"]) - due).total_seconds() < 0.01


@pytest.mark.asyncio
async def test_next_soft_slot_has_no_cut_time(db_session):
    station, playing, _, spot = await _with_soft_spot(db_session, elapsed=10, due_in=60)
    nxt = await _next_payload(db_session, station, playing)
    assert nxt["id"] == str(spot.id) and nxt["cut_at"] is None


@pytest.mark.asyncio
async def test_next_plain_song_has_no_cut_time(db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10)
    nxt = await _next_payload(db_session, station, playing)
    assert nxt["id"] == str(pending.asset_id) and nxt["cut_at"] is None


@pytest.mark.asyncio
async def test_next_item_of_the_group_on_air_has_no_cut_time(db_session):
    # Every item of a program block shares one preempt_at. With the time check on air
    # since hh:00, the Daf Yomi announcement after it still carries hh:00; pages took
    # that as "cut in now", faded the time check and joined the announcement late
    # (Main, 15:00Z 2026-11-17). It follows the item on air instead.
    station, playing, _ = await _station_with_two_tracks(db_session, elapsed_seconds=2)
    group_due = playing.started_at
    playing.preempt_at = group_due
    playing.source = "block"
    notice = Asset(id=uuid.uuid4(), title="Daf Yomi", duration=25.0,
                   file_path="https://cdn.example.com/daf.mp3", asset_type="spot")
    db_session.add(notice)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=notice.id, position=2,
                              status="pending", source="block", preempt_at=group_due))
    await db_session.commit()
    nxt = await _next_payload(db_session, station, playing)
    assert nxt["id"] == str(notice.id)
    assert nxt["cut_at"] is None
