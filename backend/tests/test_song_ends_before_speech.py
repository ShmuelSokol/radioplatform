"""A song ends completely before a sponsorship or announcement starts.

Shmuel, 2026-11-17 9:39 PM ET, chose "Song ends first, then speech": on Main the last
second of a song (its 1 s crossfade) played under the first words of a sponsorship.
A song followed by anything spoken is not crossfaded into it: the server hands over at
the song's end (0.3 s before, like every spoken hand-over) and tells the pages to fade
the song out to that end (asset.end_fade_seconds). The pages' half is checked by
frontend/e2e/audio-engine.mjs (song-ends-before-a-sponsorship).
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import upcoming_analysis
from app.services.scheduler_engine import SchedulerEngine


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    monkeypatch.setattr(upcoming_analysis, "request", lambda *a, **k: None)
    queue_module._automatic_change.clear()
    queue_module._speech_follows.clear()
    yield
    queue_module._automatic_change.clear()
    queue_module._speech_follows.clear()
    queue_module._station_crossfade.clear()


async def _none():
    return None


async def _setup(db, next_kind, next_duration=30.0, next_fields=None):
    """A 200 s song on air (audible end 198.5 s) and one item queued after it."""
    station = Station(id=uuid.uuid4(), name=f"Main {uuid.uuid4().hex[:6]}", timezone="America/New_York")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="https://cdn.example.com/s.mp3",
                 asset_type="music",
                 metadata_extra={"audio_analysis": {"cross_start_seconds": 195.5, "version": 99}})
    nxt_asset = Asset(id=uuid.uuid4(), title="Next", duration=next_duration,
                      file_path="https://cdn.example.com/n.mp3", asset_type=next_kind)
    db.add_all([station, song, nxt_asset])
    await db.flush()
    now = datetime.now(UTC)
    on_air = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                        status="playing", source="auto", started_at=now - timedelta(seconds=20))
    nxt = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=nxt_asset.id, position=2,
                     status="pending", **{"source": "auto", **(next_fields or {})})
    db.add_all([on_air, nxt])
    await db.commit()
    return station, on_air, nxt


async def _fresh(db, entry_id):
    return (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset)).where(QueueEntry.id == entry_id)
        .execution_options(populate_existing=True)
    )).unique().scalar_one()


async def _moments(db, monkeypatch, station, on_air):
    """(timer hand-over, message hand-over, switch_time) in seconds after the song's start,
    and the now-playing message."""
    armed = []
    engine = SchedulerEngine()
    monkeypatch.setattr(engine, "_schedule_precise_advance",
                        lambda sid, remaining, cross: armed.append((datetime.now(UTC), remaining, cross)))
    monkeypatch.setattr(engine, "_broadcast_queue_entry", lambda *a, **k: _none())
    await engine._advance_queue(db, station.id)
    assert len(armed) == 1, "the timer was not armed"
    at_call, remaining, cross = armed[0]
    entry = await _fresh(db, on_air.id)
    started = queue_module._aware(entry.started_at)
    timer = (at_call + timedelta(seconds=remaining - cross) - started).total_seconds()
    payload = await engine.queue_entry_payload(db, station.id, entry)
    switch = (queue_module.switch_time(entry) - started).total_seconds()
    return timer, payload["asset"]["cross_start"], switch, payload, cross


SPOKEN = [
    ("sponsorship in its slot", "spot", 30.0, {"source": "ad_slot"}),
    ("sponsorship queued by hand", "spot", 30.0, {}),
    ("announcement", "jingle", 25.0, {}),
    ("station ID (short)", "jingle", 6.0, {}),
    ("shiur", "shiur", 1800.0, {}),
    ("item of a program block", "spot", 30.0, {"source": "block"}),
]


@pytest.mark.parametrize("name,kind,duration,fields", SPOKEN, ids=[c[0] for c in SPOKEN])
@pytest.mark.asyncio
async def test_a_song_is_not_crossfaded_into_a_spoken_item(db_session, monkeypatch, name, kind, duration, fields):
    station, on_air, nxt = await _setup(db_session, kind, duration, fields)
    timer, message, switch, payload, cross = await _moments(db_session, monkeypatch, station, on_air)
    # The song's audible end is 198.5 s; the server hands over 0.3 s before it, in the
    # timer, the message and the look-ahead alike
    for label, at in (("timer", timer), ("message", message), ("switch_time", switch)):
        assert abs(at - 198.2) < 0.2, f"{name}: {label} hands over at {at}"
    assert payload["next_asset"]["id"] == str(nxt.asset_id)
    assert payload["asset"]["overlap_seconds"] == 0.0
    # The pages fade the song out over its last second, then start the spoken item
    assert payload["asset"]["end_fade_seconds"] == 1.0

    # The queue moves at that moment and not at the old crossfade point (197.5 s)
    entry = await _fresh(db_session, on_air.id)
    entry.started_at = datetime.now(UTC) - timedelta(seconds=197.8)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id, early_seconds=cross)).id == on_air.id
    entry = await _fresh(db_session, on_air.id)
    entry.started_at = datetime.now(UTC) - timedelta(seconds=198.21)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id, early_seconds=cross)).id == nxt.id


@pytest.mark.asyncio
async def test_a_song_still_crossfades_into_a_song(db_session, monkeypatch):
    station, on_air, nxt = await _setup(db_session, "music", 200.0)
    timer, message, switch, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    for at in (timer, message, switch):
        assert abs(at - 197.5) < 0.2
    assert payload["asset"]["overlap_seconds"] == 1.0
    assert payload["asset"]["end_fade_seconds"] == 0.0


@pytest.mark.asyncio
async def test_the_rule_follows_the_queue_when_it_changes(db_session, monkeypatch):
    """A sponsorship put next during the song turns the crossfade off; taking it away
    again brings the crossfade back."""
    station, on_air, song_next = await _setup(db_session, "music", 200.0)
    _, message, _, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    assert abs(message - 197.5) < 0.01 and payload["asset"]["end_fade_seconds"] == 0.0

    spot = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0, file_path="https://cdn.example.com/sp.mp3",
                 asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    song_next.position = 3
    slot = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=2, status="pending",
                      source="manual")
    db_session.add(slot)
    await db_session.commit()
    timer, message, switch, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    for at in (timer, message, switch):
        assert abs(at - 198.2) < 0.2
    assert payload["asset"]["end_fade_seconds"] == 1.0

    await db_session.delete(await _fresh(db_session, slot.id))
    await db_session.commit()
    timer, message, switch, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    for at in (timer, message, switch):
        assert abs(at - 197.5) < 0.2
    assert payload["asset"]["overlap_seconds"] == 1.0 and payload["asset"]["end_fade_seconds"] == 0.0


@pytest.mark.asyncio
async def test_a_soft_sponsorship_due_just_before_the_songs_end_is_what_follows(db_session, monkeypatch):
    """Due after the crossfade point (197.5 s) but before the song's end: with no
    crossfade into speech the song hands over later, so the sponsorship follows it."""
    station, on_air, song_next = await _setup(db_session, "music", 200.0)
    spot = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0, file_path="https://cdn.example.com/sp.mp3",
                 asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    started = queue_module._aware((await _fresh(db_session, on_air.id)).started_at)
    slot = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=40, status="pending",
                      source="ad_slot", timing_mode="soft", preempt_at=started + timedelta(seconds=197.0))
    db_session.add(slot)
    await db_session.commit()
    _, message, _, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    assert payload["next_asset"]["id"] == str(spot.id)
    assert abs(message - 198.2) < 0.01
    assert payload["asset"]["end_fade_seconds"] == 1.0


@pytest.mark.asyncio
async def test_the_fade_follows_the_station_crossfade_setting(db_session, monkeypatch):
    station, on_air, _ = await _setup(db_session, "spot", 30.0)
    station.automation_config = {"crossfade_seconds": 3}
    queue_module.note_station_crossfade(station)
    _, message, _, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    assert abs(message - 198.2) < 0.01  # the hand-over does not move with the setting
    assert payload["asset"]["end_fade_seconds"] == 3.0
    # Songs set not to overlap at all still fade out briefly before speech
    station.automation_config = {"crossfade_seconds": 0}
    queue_module.note_station_crossfade(station)
    _, message, _, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    assert abs(message - 198.2) < 0.01
    assert payload["asset"]["end_fade_seconds"] == queue_module.MIN_END_FADE_SECONDS


@pytest.mark.asyncio
async def test_a_spoken_item_on_air_has_no_end_fade(db_session, monkeypatch):
    station, on_air, _ = await _setup(db_session, "spot", 30.0)
    entry = await _fresh(db_session, on_air.id)
    entry.asset.asset_type = "spot"
    await db_session.commit()
    _, _, _, payload, _ = await _moments(db_session, monkeypatch, station, on_air)
    assert payload["asset"]["overlap_seconds"] == 0.0 and payload["asset"]["end_fade_seconds"] == 0.0


@pytest.mark.asyncio
async def test_the_stream_servers_song_overlap_is_left_alone(db_session, monkeypatch):
    """The engine reads kbr_overlap for the song's own end; what it does before a spoken
    item is decided in radio.liq, so this rule does not change what it is sent."""
    from app.services import playout_engine

    station, on_air, _ = await _setup(db_session, "spot", 30.0)
    await _moments(db_session, monkeypatch, station, on_air)
    entry = await _fresh(db_session, on_air.id)
    assert queue_module.speech_follows(entry)
    assert 'kbr_overlap="1.00"' in playout_engine.build_uri(entry, "main")
