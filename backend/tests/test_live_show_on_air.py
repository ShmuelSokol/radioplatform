"""Live shows without a broadcast server (Shmuel chose this 2026-11-16).

The host's microphone is passed straight on to the station's listener pages, and
the station's automation pauses for the show and carries on where it stopped.
"""
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.api.v1.live_shows_ws import StationLiveRelay
from app.models.asset import Asset
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.queue_entry import QueueEntry, resume_offset
from app.models.station import Station
from app.services import live_show_service


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


async def _station_playing(db):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db.add(station)
    song = Asset(id=uuid.uuid4(), title="Song", duration=240.0, file_path="s", asset_type="music")
    after = Asset(id=uuid.uuid4(), title="After", duration=200.0, file_path="a", asset_type="music")
    clock = Asset(id=uuid.uuid4(), title="Time", duration=8.0, file_path="t", asset_type="jingle")
    db.add_all([song, after, clock])
    await db.flush()
    now = datetime.now(UTC)
    db.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                   status="playing", source="auto", started_at=now - timedelta(seconds=100)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=2,
                   status="pending", source="auto"),
    ])
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live",
                    status=LiveShowStatus.SCHEDULED,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1))
    db.add(show)
    await db.commit()
    return station, show, song, after, clock


@pytest.mark.asyncio
async def test_show_pauses_the_station_and_it_carries_on_after(db_session):
    station, show, song, after, clock = await _station_playing(db_session)

    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing"))).scalars().all()
    assert playing == []  # nothing plays under the host
    # Nothing starts by itself while the host is on
    assert await queue_module._check_advance(db_session, station.id) is None

    # A time check came due during the show, long ago by its end: not played after
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock.id,
                              position=0, status="pending", timing_mode="hard",
                              preempt_at=datetime.now(UTC) - timedelta(minutes=20)))
    await db_session.commit()

    await live_show_service.end_show(db_session, show.id)
    await db_session.commit()
    on_air = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing"))).scalar_one()
    assert on_air.asset_id == song.id  # the song the show paused
    assert 90 <= resume_offset(on_air) <= 100


@pytest.mark.asyncio
async def test_start_broadcasting_puts_a_scheduled_show_on_air(db_session):
    """The Host Console's Start Broadcasting only opens the mic; it must still pause
    the station so the story under the show resumes where it stopped."""
    station, show, song, after, clock = await _station_playing(db_session)

    assert await live_show_service.start_if_scheduled(db_session, show.id) is True
    await db_session.commit()
    await db_session.refresh(show)
    assert show.status == LiveShowStatus.LIVE and show.actual_start is not None
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing"))).scalars().all()
    assert playing == []
    # Reconnecting the mic during the show does not start it again
    assert await live_show_service.start_if_scheduled(db_session, show.id) is False

    await live_show_service.end_show(db_session, show.id)
    await db_session.commit()
    on_air = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing"))).scalar_one()
    assert on_air.asset_id == song.id
    assert 90 <= resume_offset(on_air) <= 100
    # An ended show is not restarted by a stray mic connection
    assert await live_show_service.start_if_scheduled(db_session, show.id) is False


class _FakeSocket:
    def __init__(self):
        self.text, self.bytes = [], []

    async def send_text(self, t):
        self.text.append(json.loads(t))

    async def send_bytes(self, b):
        self.bytes.append(b)


@pytest.mark.asyncio
async def test_relay_passes_host_audio_to_the_station_listeners_only():
    relay = StationLiveRelay()
    here, elsewhere = _FakeSocket(), _FakeSocket()
    relay.listeners = {"st-1": {here}, "st-2": {elsewhere}}
    await relay.set_on_air("st-1", "show-1")
    await relay.audio("st-1", b"\x01\x00\x02\x00")
    await relay.set_on_air("st-1", None)
    assert [m["live"] for m in here.text] == [True, False]
    assert here.bytes == [b"\x01\x00\x02\x00"]
    assert elsewhere.text == [] and elsewhere.bytes == []


@pytest.mark.asyncio
async def test_a_dead_listener_is_dropped():
    class Dead(_FakeSocket):
        async def send_bytes(self, b):
            raise RuntimeError("gone")

    relay = StationLiveRelay()
    ok, dead = _FakeSocket(), Dead()
    relay.listeners = {"st-1": {ok, dead}}
    await relay.audio("st-1", b"\x00\x00")
    assert relay.listeners["st-1"] == {ok}
    assert ok.bytes == [b"\x00\x00"]
