"""HS (hard start) off and Spots mode off, on air: checklist rows F-MODES-2, -6, -27, -30
(docs/test-checklist.md section N, wiki/station-modes.md).

"Time passes" by moving the rows' times back, so each check runs at once.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry, resume_offset
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services import playout_engine
from app.services.spot_schedule_service import SAFETY_MARGIN, reconcile_spot_slots
from tests import test_engine_backend as _engine_tests
from tests.test_engine_backend import _add, _fresh, _next, _on_air

eng = _engine_tests.eng  # the engine fixture (faked engine commands)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    queue_module._automatic_change.clear()
    yield
    queue_module._automatic_change.clear()


async def _row(db, entry_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.id == entry_id).execution_options(populate_existing=True)
    )).scalar_one()


async def _shift(db, *entry_ids, started=None, due=None):
    """Move times back: `started` = seconds ago it started, `due` = seconds from now."""
    for eid in entry_ids:
        row = await _row(db, eid)
        if started is not None:
            row.started_at = datetime.now(UTC) - timedelta(seconds=started)
        if due is not None:
            row.preempt_at = datetime.now(UTC) + timedelta(seconds=due)
    await db.commit()


async def _modes(client, auth_headers, station_id, **body):
    r = await client.post(f"/api/v1/stations/{station_id}/queue/modes", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["modes"]


async def _browser_station(db, *, on_air_kind="music", on_air_duration=200.0, config=None):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", automation_config=config or {})
    on_air_asset = Asset(id=uuid.uuid4(), title="On air", duration=on_air_duration,
                         file_path="https://cdn.example.com/on.mp3", asset_type=on_air_kind)
    song = Asset(id=uuid.uuid4(), title="Next song", duration=200.0,
                 file_path="https://cdn.example.com/s.mp3", asset_type="music")
    check = Asset(id=uuid.uuid4(), title="Time", duration=8.0, file_path="https://cdn.example.com/t.mp3",
                  asset_type="jingle", category="time_announcement")
    block_item = Asset(id=uuid.uuid4(), title="Block ID", duration=15.0,
                       file_path="https://cdn.example.com/b.mp3", asset_type="jingle")
    db.add_all([station, on_air_asset, song, check, block_item])
    await db.flush()
    due = datetime.now(UTC) + timedelta(minutes=2)
    on_air = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=on_air_asset.id, position=1,
                        status="playing", source="auto", started_at=datetime.now(UTC) - timedelta(seconds=10))
    nxt = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                     status="pending", source="auto")
    tc = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=50,
                    status="pending", source="auto", preempt_at=due, timing_mode="hard")
    blk = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=block_item.id, position=51,
                     status="pending", source="block", preempt_at=due, timing_mode="hard")
    db.add_all([on_air, nxt, tc, blk])
    await db.commit()
    return station, on_air, nxt, tc, blk


# ── F-MODES-2: the time check waits for the song, then plays straight after it ──


@pytest.mark.asyncio
async def test_hs_off_time_check_waits_for_the_song_then_follows_it(client, auth_headers, db_session):
    """F-MODES-2 (server, browser player): at hh:00 the song on air keeps playing; at the
    song's hand-over the time check (then the hard block item) starts, within 1 s of it,
    and listeners were told in advance that it comes next (no silence while loading)."""
    station, song, nxt, tc, blk = await _browser_station(db_session)
    assert (await _modes(client, auth_headers, station.id, hard_start=False))["hard_start"] is False

    # hh:00 has come: the song stays on air
    await _shift(db_session, tc.id, blk.id, due=-1)
    await _shift(db_session, song.id, started=120)
    assert (await queue_module._check_advance(db_session, station.id)).id == song.id
    assert (await _row(db_session, tc.id)).status == "pending"

    # What listeners pre-load as "next" is the time check, not the next song
    current = await _row(db_session, song.id)
    predicted = await queue_module.predict_next_entry(db_session, station.id, current)
    assert predicted.id == tc.id

    # The song reaches its hand-over point: the time check starts at that moment
    cross = queue_module.handover_seconds(current)
    await _shift(db_session, song.id, started=200 - cross + 0.01)
    switch = queue_module.switch_time(await _row(db_session, song.id))
    started = await queue_module._check_advance(db_session, station.id, early_seconds=cross)
    assert started.id == tc.id
    assert abs((queue_module._aware(started.started_at) - switch).total_seconds()) < 1.0
    # ... played whole: nothing cuts it, and the block item follows at its end
    check_row = await _row(db_session, tc.id)
    await _shift(db_session, tc.id, started=8.0 - queue_module.handover_seconds(check_row) + 0.01)
    after = await queue_module._check_advance(db_session, station.id, early_seconds=queue_module.SHORT_ITEM_HANDOVER_SECONDS)
    assert after.id == blk.id
    assert (await _row(db_session, tc.id)).status == "played"


@pytest.mark.asyncio
async def test_hs_off_engine_gets_the_time_check_as_the_next_track(client, auth_headers, db_session, eng):
    """F-MODES-2 (engine half): HS off, the time check is not handed to the timed lane;
    once due it is the engine's next track after the song on air."""
    station, entries, engine, _ = eng
    await _next(client, station)
    await _on_air(client, station, entries[0].id)
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time", position=50,
                       timing_mode="hard", preempt_at=datetime.now(UTC) + timedelta(seconds=120))
    await _modes(client, auth_headers, station.id, hard_start=False)
    await _shift(db_session, clock.id, due=30)
    st = await db_session.get(Station, station.id)
    await playout_engine.tick(db_session, st)
    assert engine.timed() == []
    await _shift(db_session, clock.id, due=-1)
    r = await _next(client, station, str(entries[0].id))
    assert r.status_code == 200
    assert r.json()["entry_id"] == str(clock.id)


# ── F-MODES-6: the known engine limit ────────────────────────────────────────


@pytest.mark.asyncio
async def test_hs_off_cannot_recall_an_item_already_handed_to_the_engine(client, auth_headers, db_session, eng):
    """F-MODES-6. Known limit (wiki/station-modes.md): a hard item handed to the engine
    (45 s ahead) cannot be cancelled: HS off only flushes the pre-loaded track, the timed
    lane keeps it and it still starts on the second. A later item is not handed while HS is
    off; HS on in time hands it with its original time."""
    station, _, engine, _ = eng
    soon = await _add(db_session, station, title="Time", duration=8.0, kind="time", position=50,
                      timing_mode="hard", preempt_at=datetime.now(UTC) + timedelta(seconds=30))
    later_at = datetime.now(UTC) + timedelta(seconds=40)
    later = await _add(db_session, station, title="Weather", duration=30.0, kind="weather", position=51,
                       timing_mode="hard", preempt_at=later_at + timedelta(minutes=10))
    st = await db_session.get(Station, station.id)
    await playout_engine.tick(db_session, st)
    assert [c["id"] for c in engine.timed()] == [str(soon.id)]

    engine.sent.clear()
    await _modes(client, auth_headers, station.id, hard_start=False)
    assert engine.paths() == ["/flush"]  # no way to take the timed item back
    assert (await _fresh(db_session, soon.id)).timing_mode == "soft"
    # The engine plays it on the second anyway, and the site follows what really aired
    await _on_air(client, station, soon.id, lane="timed")
    assert (await _fresh(db_session, soon.id)).status == "playing"

    # The later one comes inside the 45 s window while HS is off: not handed
    row = await _fresh(db_session, later.id)
    row.preempt_at = later_at
    await db_session.commit()
    engine.sent.clear()
    await playout_engine.tick(db_session, await db_session.get(Station, station.id))
    assert engine.timed() == []
    # HS on before its time: handed for its original second
    await _modes(client, auth_headers, station.id, hard_start=True)
    await playout_engine.tick(db_session, await db_session.get(Station, station.id))
    timed = engine.timed()
    assert [c["id"] for c in timed] == [str(later.id)]
    assert abs(timed[0]["at"] - later_at.timestamp()) < 0.01


# ── F-MODES-30: HS off with a shiur on air, whatever the long-item rule ──────


@pytest.mark.parametrize("policy", ["pause", "wait", "cut"])
@pytest.mark.asyncio
async def test_hs_off_time_check_waits_for_the_shiur_whatever_the_rule(client, auth_headers, db_session, policy):
    """F-MODES-30: HS off: at hh:00 the shiur plays on (no pause, no cut, no resume entry)
    and the time check follows its end. HS on again before hh:00: the rule is back."""
    station, shiur, nxt, tc, blk = await _browser_station(
        db_session, on_air_kind="shiur", on_air_duration=3600.0,
        config={"timed_during_long_items": policy})
    await _shift(db_session, shiur.id, started=600)
    await _modes(client, auth_headers, station.id, hard_start=False)
    await _shift(db_session, tc.id, blk.id, due=-1)
    assert (await queue_module._check_advance(db_session, station.id)).id == shiur.id
    pending = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "pending"))).scalars().all()
    assert not [e for e in pending if resume_offset(e) > 0]
    # The shiur ends: the time check is next
    await _shift(db_session, shiur.id, started=3600 - 3.0 + 0.01)
    assert (await queue_module._check_advance(db_session, station.id, early_seconds=3.0)).id == tc.id


@pytest.mark.parametrize("policy", ["pause", "wait", "cut"])
@pytest.mark.asyncio
async def test_hs_on_again_before_the_hour_brings_the_long_item_rule_back(client, auth_headers, db_session, policy):
    station, shiur, nxt, tc, blk = await _browser_station(
        db_session, on_air_kind="shiur", on_air_duration=3600.0,
        config={"timed_during_long_items": policy})
    await _shift(db_session, shiur.id, started=600)
    await _modes(client, auth_headers, station.id, hard_start=False)
    await _modes(client, auth_headers, station.id, hard_start=True)
    assert (await _row(db_session, tc.id)).timing_mode == "hard"
    await _shift(db_session, tc.id, blk.id, due=-1)
    on_air = await queue_module._check_advance(db_session, station.id)
    resumed = [e for e in (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "pending"))).scalars().all()
        if e.asset_id == shiur.asset_id and resume_offset(e) > 500]
    if policy == "wait":
        assert on_air.id == shiur.id and not resumed
    elif policy == "pause":
        assert on_air.id == tc.id and len(resumed) == 1
    else:
        assert on_air.id == tc.id and not resumed
        assert (await _row(db_session, shiur.id)).status == "played"


# ── F-MODES-27: Spots mode off and the 2-minute margin ───────────────────────


@pytest.mark.asyncio
async def test_spots_off_keeps_the_slot_inside_the_margin_and_makes_nothing_up(client, auth_headers, db_session):
    """F-MODES-27: a sponsorship due within 2 minutes still airs; every later one is gone;
    Spots on again plans only from then on (the missed plays are not made up)."""
    station, song, nxt, tc, blk = await _browser_station(db_session)
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    spot = Asset(id=uuid.uuid4(), title="Bagel King", duration=30.0, file_path="https://cdn.example.com/k.mp3",
                 asset_type="spot")
    db_session.add_all([sponsor, spot])
    await db_session.flush()
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                                dayparts="morning,afternoon,evening,overnight", plays_per_daypart=3,
                                start_date=date.today() - timedelta(days=1), end_date=date.today() + timedelta(days=5)))
    near = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=40, status="pending",
                      source="ad_slot", timing_mode="soft",
                      preempt_at=datetime.now(UTC) + timedelta(seconds=60))
    db_session.add(near)
    await db_session.commit()
    await reconcile_spot_slots(db_session, station.id)
    await db_session.commit()

    async def pending_slots():
        return (await db_session.execute(select(QueueEntry).where(
            QueueEntry.station_id == station.id, QueueEntry.source == "ad_slot", QueueEntry.status == "pending")
            .execution_options(populate_existing=True))).scalars().all()

    assert len(await pending_slots()) > 1
    await _modes(client, auth_headers, station.id, spots=False)
    assert [e.id for e in await pending_slots()] == [near.id]

    # Its time comes and the song ends: it airs
    await _shift(db_session, near.id, due=-1)
    await _shift(db_session, song.id, started=200 - 3.0 + 0.01)
    assert (await queue_module._check_advance(db_session, station.id, early_seconds=3.0)).id == near.id

    # Spots on again an hour later: nothing planned for the hour that went by
    st = await db_session.get(Station, station.id)
    st.automation_config = {**(st.automation_config or {}), "spots_on": True}
    await db_session.commit()
    later = datetime.now(UTC) + timedelta(hours=1)
    await reconcile_spot_slots(db_session, station.id, now=later)
    await db_session.commit()
    slots = await pending_slots()
    assert slots, "Spots on plans the schedule again"
    assert all(queue_module._aware(e.preempt_at) > later for e in slots)
    assert SAFETY_MARGIN == timedelta(minutes=2)
