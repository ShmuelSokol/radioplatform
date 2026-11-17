"""next_asset.cut_at in the now-playing message (PR 157).

Pages start a pre-loaded timed item at the moment it cuts in, instead of waiting a
second or more for the server's message. cut_at is that moment: a hard item's time,
a soft item's limit (never before its own time), or None when it follows the item
on air. test_next_follows_soft_slot.py covers a hard block item, a soft slot with no
limit and a plain song; these cover the soft limit, a hard ad slot and the message a
listener actually reads (the polling answer, which is also the socket's first message).
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.websocket as ws_mod
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


@pytest.fixture(autouse=True)
def _fresh_join_cache():
    ws_mod._join_cache.clear()
    yield
    ws_mod._join_cache.clear()


async def _with_timed(db, *, source, timing_mode=None, due_in=60.0, hard_by_in=None, duration=5.0):
    """A song 10 s in, and a timed item due `due_in` s from now (before the song ends)."""
    station, playing, _ = await _station_with_two_tracks(db, elapsed_seconds=10)
    clip = Asset(id=uuid.uuid4(), title="Timed", duration=duration,
                 file_path="https://cdn.example.com/timed.mp3", asset_type="spot")
    db.add(clip)
    await db.flush()
    now = datetime.now(UTC)
    due = now + timedelta(seconds=due_in)
    hard_by = now + timedelta(seconds=hard_by_in) if hard_by_in is not None else None
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=3,
                      status="pending", source=source, timing_mode=timing_mode,
                      preempt_at=due, hard_by=hard_by))
    await db.commit()
    return station, playing, clip, due, hard_by


async def _next_asset(db, station, playing):
    await db.refresh(playing, ["asset"])
    payload = await SchedulerEngine().queue_entry_payload(db, station.id, playing)
    return payload["next_asset"]


def _at(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_soft_item_with_a_limit_cuts_in_at_its_limit(db_session):
    # Due in 60 s, must be on by 90 s: the song is cut only at the limit
    station, playing, clip, _, hard_by = await _with_timed(
        db_session, source="ad_slot", timing_mode="soft", due_in=60, hard_by_in=90)
    nxt = await _next_asset(db_session, station, playing)
    assert nxt["id"] == str(clip.id)
    assert nxt["cut_at"] is not None
    assert abs((_at(nxt["cut_at"]) - hard_by).total_seconds()) < 0.01


@pytest.mark.asyncio
async def test_soft_item_limit_before_its_time_cuts_in_at_its_time(db_session):
    # A limit earlier than the item's own time never makes it cut in early
    station, playing, clip, due, _ = await _with_timed(
        db_session, source="ad_slot", timing_mode="soft", due_in=60, hard_by_in=30)
    nxt = await _next_asset(db_session, station, playing)
    assert nxt["id"] == str(clip.id)
    assert abs((_at(nxt["cut_at"]) - due).total_seconds()) < 0.01


@pytest.mark.asyncio
async def test_hard_ad_slot_cuts_in_at_its_time(db_session):
    # timing_mode "hard" wins over the ad_slot source (soft by default)
    station, playing, clip, due, _ = await _with_timed(
        db_session, source="ad_slot", timing_mode="hard", due_in=60)
    nxt = await _next_asset(db_session, station, playing)
    assert nxt["id"] == str(clip.id)
    assert abs((_at(nxt["cut_at"]) - due).total_seconds()) < 0.01


@pytest.mark.asyncio
async def test_listener_poll_answer_carries_cut_at(client, db_session):
    # GET /now-playing/{station} is what a listener polls when the socket fails, and
    # the same message the socket sends on joining: it must carry cut_at too
    station, _, clip, due, _ = await _with_timed(db_session, source="block", due_in=60)
    resp = await client.get(f"/api/v1/now-playing/{station.id}")
    assert resp.status_code == 200
    nxt = resp.json()["next_asset"]
    assert nxt["id"] == str(clip.id)
    assert abs((_at(nxt["cut_at"]) - due).total_seconds()) < 0.01
    # with a timezone, so every browser reads the same moment
    assert nxt["cut_at"].endswith("+00:00")
