"""The hand-over moment is the same everywhere on the server: checklist rows F-HANDOVER-4,
-7, -9, -10, -15, -16 (docs/test-checklist.md section N, wiki/audio-engine.md).

Four places compute when the item on air hands over: the scheduler's precise timer
(SchedulerEngine._advance_queue), the now-playing message listeners act on
(started_at + asset.cross_start), switch_time, and the look-ahead (predict_next_entry).
The queue advance itself (_check_advance with early_seconds) must move at that moment,
not before. The pages' half (useAudioEngine.ts) is checked by frontend/e2e/audio-engine.mjs.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import upcoming_analysis
from app.services.scheduler_engine import SchedulerEngine

AGREE_SECONDS = 0.2


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    monkeypatch.setattr(upcoming_analysis, "request", lambda *a, **k: None)
    queue_module._automatic_change.clear()
    yield
    queue_module._automatic_change.clear()


def _analysis(cross_start):
    return {"audio_analysis": {"cross_start_seconds": cross_start, "version": 99}}


# (name, stored duration, metadata, asset type, entry fields, expected hand-over seconds before the end)
CASES = [
    ("song with analysis", 200.0, _analysis(195.5), "music", {}, 4.5),
    ("song without analysis", 200.0, None, "music", {}, 3.0),
    ("station ID (short)", 6.0, None, "jingle", {}, queue_module.SHORT_ITEM_HANDOVER_SECONDS),
    ("time check (timed)", 8.0, None, "jingle", {"timing_mode": "hard", "preempt_at": True},
     queue_module.SHORT_ITEM_HANDOVER_SECONDS),
    ("block item 30 s", 30.0, None, "jingle", {"source": "block"}, queue_module.SHORT_ITEM_HANDOVER_SECONDS),
    # F-HANDOVER-15: none stored = 180 s, handed over 3 s before that (not at half)
    ("no stored length", None, None, "music", {}, 3.0),
    # F-HANDOVER-10: a 25 s PSA queued as a song crossfades like a song
    ("PSA as a song", 25.0, None, "spot", {}, 3.0),
]


async def _setup(db, duration, metadata, kind, fields, started_ago=10.0):
    station = Station(id=uuid.uuid4(), name=f"Main {uuid.uuid4().hex[:6]}", timezone="America/New_York")
    asset = Asset(id=uuid.uuid4(), title="On air", duration=duration, metadata_extra=metadata,
                  file_path="https://cdn.example.com/on.mp3", asset_type=kind)
    song = Asset(id=uuid.uuid4(), title="Next", duration=200.0, file_path="https://cdn.example.com/n.mp3",
                 asset_type="music")
    db.add_all([station, asset, song])
    await db.flush()
    now = datetime.now(UTC)
    fields = dict(fields)
    if fields.get("preempt_at") is True:
        fields["preempt_at"] = now - timedelta(seconds=started_ago)
    fields.setdefault("source", "auto")
    on_air = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=1,
                        status="playing", started_at=now - timedelta(seconds=started_ago), **fields)
    nxt = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                     status="pending", source="auto")
    db.add_all([on_air, nxt])
    await db.commit()
    return station, on_air, nxt


async def _fresh(db, entry_id):
    from sqlalchemy.orm import joinedload
    return (await db.execute(
        select(QueueEntry).options(joinedload(QueueEntry.asset)).where(QueueEntry.id == entry_id)
        .execution_options(populate_existing=True)
    )).unique().scalar_one()


@pytest.mark.parametrize("name,duration,metadata,kind,fields,expected", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_timer_message_switch_time_and_advance_agree(db_session, monkeypatch, name, duration, metadata,
                                                          kind, fields, expected):
    """F-HANDOVER-4 (with -10, -15): every place gives the same moment, within 0.2 s, and
    it is the expected one for the item."""
    length = duration or queue_module.DEFAULT_DURATION
    station, on_air, nxt = await _setup(db_session, duration, metadata, kind, fields, started_ago=min(10.0, length / 4))
    started = queue_module._aware(on_air.started_at)
    expected_at = started + timedelta(seconds=length - expected)

    # 1. The scheduler's precise timer
    armed = []
    engine = SchedulerEngine()
    monkeypatch.setattr(engine, "_schedule_precise_advance",
                        lambda sid, remaining, cross: armed.append((datetime.now(UTC), remaining, cross)))
    monkeypatch.setattr(engine, "_broadcast_queue_entry", lambda *a, **k: _none())
    await engine._advance_queue(db_session, station.id)
    assert len(armed) == 1, "the timer was not armed"
    at_call, remaining, cross = armed[0]
    timer_at = at_call + timedelta(seconds=remaining - cross)

    # 2. The now-playing message listeners act on
    entry = await _fresh(db_session, on_air.id)
    payload = await engine.queue_entry_payload(db_session, station.id, entry)
    message_at = queue_module._aware(datetime.fromisoformat(payload["started_at"])) + timedelta(
        seconds=payload["asset"]["cross_start"])
    ends_at = queue_module._aware(datetime.fromisoformat(payload["ends_at"]))

    # 3. switch_time (also what predict_next_entry looks at)
    switch = queue_module.switch_time(entry)

    for label, at in (("timer", timer_at), ("now-playing cross_start", message_at), ("switch_time", switch)):
        assert abs((at - expected_at).total_seconds()) < AGREE_SECONDS, f"{name}: {label} {at} vs {expected_at}"
    assert abs((ends_at - started).total_seconds() - length) < 0.01
    # Not before half of the item (the old rule cut a 5 s time check at 2.5 s)
    assert (switch - started).total_seconds() >= length / 2

    # 4. The advance itself moves at that moment and not 0.2 s before
    entry.started_at = datetime.now(UTC) - timedelta(seconds=length - expected - AGREE_SECONDS)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id, early_seconds=cross)).id == on_air.id
    entry = await _fresh(db_session, on_air.id)
    entry.started_at = datetime.now(UTC) - timedelta(seconds=length - expected + 0.01)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id, early_seconds=cross)).id == nxt.id


async def _none():
    return None


@pytest.mark.asyncio
async def test_spoken_item_with_analysis_hands_over_at_its_last_word(db_session):
    """F-HANDOVER-10 (server half): a 25 s PSA queued as a song whose analysis puts the end
    of speech at 24.6 s hands over there, not 3 s early with words under the fade."""
    station, on_air, _ = await _setup(db_session, 25.0, _analysis(24.6), "spot", {})
    entry = await _fresh(db_session, on_air.id)
    assert abs(queue_module.handover_seconds(entry) - 0.4) < 1e-9


@pytest.mark.asyncio
async def test_wrong_stored_length_still_one_moment_on_the_server(db_session, monkeypatch):
    """F-HANDOVER-9 (server half): a 6 s file stored as 25 s. The server's timer, message and
    advance all use the stored 25 s (hand-over at 22 s, crossfaded like a song); the page
    caps its own hand-over at the real file's end (handOverPoint in useAudioEngine.ts), so
    the page moves on at ~5.7 s and the server 16 s later. Only fixing the stored length
    (duration_backfill measures items with none, not wrong ones) makes them meet."""
    station, on_air, _ = await _setup(db_session, 25.0, None, "jingle", {})
    entry = await _fresh(db_session, on_air.id)
    assert queue_module.handover_seconds(entry) == 3.0
    engine = SchedulerEngine()
    payload = await engine.queue_entry_payload(db_session, station.id, entry)
    assert payload["asset"]["cross_start"] == 22.0
    assert queue_module.switch_time(entry) == queue_module._aware(entry.started_at) + timedelta(seconds=22.0)


@pytest.mark.asyncio
async def test_operator_next_cuts_a_short_item_at_once(client, auth_headers, db_session, monkeypatch):
    """F-HANDOVER-7 (server half): Next 2 s into a 6 s station ID changes at once, whatever
    the fade, and the message carries the fade so the page cuts instead of letting the
    short item finish (operatorFade wins over letFinish in useAudioEngine.ts)."""
    for fade, seconds in queue_module.SKIP_FADE_SECONDS.items():
        station, on_air, nxt = await _setup(db_session, 6.0, None, "jingle", {}, started_ago=2.0)
        sent = []

        async def record(db, station_id, entry_id, change_fade_seconds=None):
            sent.append((str(entry_id), change_fade_seconds))

        monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", record)
        body = (await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade={fade}",
                                  headers=auth_headers)).json()
        assert body["message"] == "Skipped", (fade, body)
        assert body["fade_seconds"] == seconds
        assert sent == [(str(nxt.id), seconds)]
        assert (await _fresh(db_session, on_air.id)).status == "skipped"


@pytest.mark.asyncio
async def test_listeners_preload_what_the_server_picks_at_the_handover(db_session):
    """F-HANDOVER-16: a soft sponsorship falls due during the song: the look-ahead names it
    (not the next song) and the queue really picks it at the hand-over. One due after the
    hand-over is not named; the next song is."""
    station, on_air, nxt = await _setup(db_session, 200.0, None, "music", {})
    spot = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0, file_path="https://cdn.example.com/sp.mp3",
                 asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    late = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=30, status="pending",
                      source="ad_slot", timing_mode="soft", preempt_at=datetime.now(UTC) + timedelta(minutes=10))
    db_session.add(late)
    await db_session.commit()
    entry = await _fresh(db_session, on_air.id)
    assert (await queue_module.predict_next_entry(db_session, station.id, entry)).id == nxt.id

    due_in_song = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=31,
                             status="pending", source="ad_slot", timing_mode="soft",
                             preempt_at=datetime.now(UTC) + timedelta(seconds=60))
    db_session.add(due_in_song)
    await db_session.commit()
    predicted = await queue_module.predict_next_entry(db_session, station.id, entry)
    assert predicted.id == due_in_song.id
    payload = await SchedulerEngine().queue_entry_payload(db_session, station.id, entry)
    assert payload["next_asset"]["id"] == str(spot.id)

    # Time passes to the hand-over: the server starts exactly what was pre-loaded
    shift = (queue_module.switch_time(entry) - datetime.now(UTC)).total_seconds() + 0.01
    for eid in (on_air.id, due_in_song.id):
        row = await _fresh(db_session, eid)
        if row.started_at is not None:
            row.started_at -= timedelta(seconds=shift)
        row.preempt_at = row.preempt_at - timedelta(seconds=shift) if row.preempt_at else None
    await db_session.commit()
    started = await queue_module._check_advance(db_session, station.id, early_seconds=3.0)
    assert started.id == predicted.id

