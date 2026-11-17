"""Soft timed items (the :30 weather, sponsorships) during a shiur or story.

KBR Jerusalem's 7:30 weather aired at 7:49 behind a 49-minute shiur. Shmuel's rule
(2026-11-17): if the shiur has more than 5 minutes left, pause it; under 5 minutes,
wait. Checklist rows F-HANDOVER-21 and F-SOFTPAUSE-* (docs/test-checklist.md).
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry, resume_offset
from app.models.station import Station
from app.services import long_items, playout_engine
from tests import test_engine_backend as _engine_tests
from tests.test_engine_backend import _add, _fresh, _next, _on_air

eng = _engine_tests.eng  # the engine fixture (faked engine commands)


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def nothing(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)


def _asset(db, title, kind="jingle", duration=30.0):
    a = Asset(id=uuid.uuid4(), title=title, duration=float(duration), asset_type=kind,
              file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(a)
    return a


async def _setup(db, *, left, policy=None, kind="shiur", duration=49 * 60, soft_items=1,
                 soft_source=None, due_ago=1.0, hard_by_in=None, talk_preempt_at=None):
    """A shiur on air with `left` seconds to go and a soft group due `due_ago` s ago."""
    config = {"timed_during_long_items": policy} if policy else {}
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem",
                      automation_config=config)
    db.add(station)
    talk = _asset(db, "Yoma 79", kind=kind, duration=duration)
    softs = [_asset(db, f"Soft {i}") for i in range(soft_items)]
    song = _asset(db, "Song", kind="music", duration=200)
    await db.flush()
    now = datetime.now(UTC)
    due = now - timedelta(seconds=due_ago)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=talk.id, position=1,
                      status="playing", source="auto", preempt_at=due if talk_preempt_at else None,
                      timing_mode="hard" if talk_preempt_at else None,
                      started_at=now - timedelta(seconds=duration - left)))
    soft_entries = []
    for i, a in enumerate(softs):
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=2 + i,
                       status="pending", preempt_at=due,
                       source=soft_source or "block",
                       timing_mode=None if soft_source == "ad_slot" else "soft",
                       hard_by=(now + timedelta(seconds=hard_by_in)) if hard_by_in else None)
        soft_entries.append(e)
    db.add_all(soft_entries)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id,
                      position=2 + soft_items, status="pending", source="auto"))
    await db.commit()
    return station, talk, softs, song


async def _finish_current(db, station_id):
    current = (await db.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True))).scalar_one()
    current.started_at = datetime.now(UTC) - timedelta(seconds=current.asset.duration + 1)
    await db.commit()
    return await queue_module._check_advance(db, station_id)


async def _pending(db, station_id):
    return (await db.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).execution_options(populate_existing=True))).scalars().all()


# ── Browser player ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_weather_pauses_a_shiur_with_more_than_five_minutes_left(db_session):
    """F-SOFTPAUSE-1: 19 min left at :30: the weather plays now, the shiur carries on
    where it stopped, then the song."""
    station, talk, (weather,), song = await _setup(db_session, left=19 * 60)
    started = await queue_module._check_advance(db_session, station.id)
    assert started.asset_id == weather.id
    resumed = await _finish_current(db_session, station.id)
    assert resumed.asset_id == talk.id
    assert abs(resume_offset(resumed) - (30 * 60 - long_items.RESUME_REWIND_SECONDS)) < 3
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
async def test_under_five_minutes_left_the_weather_waits(db_session):
    """F-SOFTPAUSE-2: 4 min left: the shiur plays to its end, then the weather; the
    shiur is not queued again."""
    station, talk, (weather,), song = await _setup(db_session, left=4 * 60)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id
    assert (await _finish_current(db_session, station.id)).asset_id == weather.id
    assert (await _finish_current(db_session, station.id)).asset_id == song.id
    assert all(e.asset_id != talk.id for e in await _pending(db_session, station.id))


@pytest.mark.asyncio
async def test_exactly_five_minutes_left_waits(db_session):
    """F-SOFTPAUSE-3: the line is "more than 5 minutes": at 5:00 (or a little under) it waits."""
    station, talk, _, _ = await _setup(db_session, left=5 * 60 - 1)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id


@pytest.mark.asyncio
async def test_just_over_five_minutes_left_pauses(db_session):
    station, talk, (weather,), _ = await _setup(db_session, left=5 * 60 + 20)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == weather.id


@pytest.mark.asyncio
async def test_a_group_of_sponsorships_plays_together_then_the_shiur_once(db_session):
    """F-SOFTPAUSE-4: three sponsorship slots (ad_slot, no timing_mode) at :30 play one
    after another, then the shiur resumes once."""
    station, talk, softs, song = await _setup(db_session, left=20 * 60, soft_items=3,
                                              soft_source="ad_slot")
    order = [(await queue_module._check_advance(db_session, station.id)).asset_id]
    for _ in range(4):
        order.append((await _finish_current(db_session, station.id)).asset_id)
    assert order == [s.id for s in softs] + [talk.id, song.id]


@pytest.mark.asyncio
async def test_soft_with_a_limit_does_not_wait_for_the_limit(db_session):
    """F-SOFTPAUSE-5: a soft block with a 10-minute limit still goes in now (the rule is
    about the shiur's time left, not the block's limit)."""
    station, talk, (weather,), _ = await _setup(db_session, left=25 * 60, hard_by_in=600)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == weather.id


@pytest.mark.asyncio
async def test_wait_station_still_waits_for_the_whole_shiur(db_session):
    """F-SOFTPAUSE-6: a station set to "wait" lets the shiur finish, even with 20 min left."""
    station, talk, (weather,), _ = await _setup(db_session, left=20 * 60, policy="wait")
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id
    assert (await _finish_current(db_session, station.id)).asset_id == weather.id


@pytest.mark.asyncio
async def test_cut_station_cuts_the_shiur(db_session):
    """F-SOFTPAUSE-7: a "cut" station cuts the shiur for the soft item and does not resume it."""
    station, talk, (weather,), song = await _setup(db_session, left=20 * 60, policy="cut")
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == weather.id
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
async def test_songs_still_finish_before_a_soft_item(db_session):
    """F-SOFTPAUSE-8: music is unchanged: the weather waits for the song, however long."""
    station, song_on_air, (weather,), _ = await _setup(db_session, left=6 * 60, kind="music",
                                                       duration=7 * 60)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == song_on_air.id
    pending = {e.asset_id: e for e in await _pending(db_session, station.id)}
    assert pending[weather.id].timing_mode == "soft"


@pytest.mark.asyncio
async def test_resumed_shiur_is_paused_again_by_the_next_soft_item(db_session):
    """F-SOFTPAUSE-9: paused at :30, resumed, and at :45 paused again from the right
    point. The :45 item sits at a later queue position than the song: the shiur must
    still come straight after it, before the song (found by this test)."""
    station, talk, (weather,), song = await _setup(db_session, left=25 * 60)
    await queue_module._check_advance(db_session, station.id)
    resumed = await _finish_current(db_session, station.id)
    assert resumed.asset_id == talk.id
    first_offset = resume_offset(resumed)
    # 15 minutes later the :45 item is due (10 min of the shiur still to go)
    resumed.started_at = resumed.started_at - timedelta(minutes=15)
    second = _asset(db_session, "Sponsor :45")
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=second.id,
                              position=50, status="pending", source="ad_slot",
                              preempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == second.id
    again = await _finish_current(db_session, station.id)
    assert again.asset_id == talk.id
    assert abs(resume_offset(again) - (first_offset + 15 * 60 - long_items.RESUME_REWIND_SECONDS)) < 4


@pytest.mark.asyncio
async def test_soft_item_in_the_shiurs_own_group_does_not_pause_it(db_session):
    """F-SOFTPAUSE-10: the shiur is itself part of the :30 block: the block's next items
    follow it, they do not cut it."""
    station, talk, _, _ = await _setup(db_session, left=20 * 60, due_ago=120, talk_preempt_at=True)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id


@pytest.mark.asyncio
async def test_no_music_inside_a_blackout(db_session):
    """F-SOFTPAUSE-11: during Shabbos a soft sponsorship never pauses the (silence) item."""
    station, talk, (weather,), _ = await _setup(db_session, left=20 * 60, kind="story")
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", reason="shabbos", is_blackout=True,
                                 start_datetime=datetime.now(UTC) - timedelta(hours=1),
                                 end_datetime=datetime.now(UTC) + timedelta(hours=20),
                                 affected_stations={"station_ids": [str(station.id)]}))
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air is None or on_air.asset_id != weather.id


@pytest.mark.asyncio
async def test_hs_off_soft_items_keep_waiting(db_session):
    """F-SOFTPAUSE-15: with HS off nothing cuts in, so the weather waits for the shiur."""
    station, talk, (weather,), _ = await _setup(db_session, left=20 * 60)
    st = await db_session.get(Station, station.id)
    st.automation_config = {"hard_start": False}
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id
    assert (await _finish_current(db_session, station.id)).asset_id == weather.id


@pytest.mark.asyncio
async def test_story_counts_as_long(db_session):
    """F-SOFTPAUSE-12: stories (All Day Stories) follow the same rule."""
    station, talk, (weather,), _ = await _setup(db_session, left=15 * 60, kind="story")
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == weather.id


# ── Playout engine ───────────────────────────────────────────────────────────


async def _engine_shiur(client, db, station, *, left, policy=None, soft_in=30):
    st = await db.get(Station, station.id)
    st.automation_config = {"timed_during_long_items": policy} if policy else {}
    shiur = await _add(db, station, title="Shiur", duration=3600.0, kind="shiur", position=0)
    await _next(client, station)
    await _on_air(client, station, shiur.id)
    row = await _fresh(db, shiur.id)
    row.started_at = datetime.now(UTC) - timedelta(seconds=3600 - left)
    await db.commit()
    weather = await _add(db, station, title="Weather", duration=30.0, kind="jingle", position=50,
                         source="block", timing_mode="soft",
                         preempt_at=datetime.now(UTC) + timedelta(seconds=soft_in))
    return st, shiur, weather


@pytest.mark.asyncio
async def test_engine_soft_item_pauses_a_long_shiur(client, db_session, eng):
    """F-SOFTPAUSE-13: on the engine the :30 weather is handed to the timed lane ahead of
    time and the shiur is queued to carry on after it."""
    station, _, engine, _ = eng
    st, shiur, weather = await _engine_shiur(client, db_session, station, left=20 * 60)
    await playout_engine.tick(db_session, st)
    assert [c["id"] for c in engine.timed()] == [str(weather.id)]
    await _on_air(client, station, weather.id, lane="timed")
    pending = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .execution_options(populate_existing=True))).scalars().all()
    assert [e for e in pending if e.asset_id == shiur.asset_id and resume_offset(e) > 2000]


@pytest.mark.asyncio
async def test_engine_soft_item_waits_when_the_shiur_is_nearly_over(client, db_session, eng):
    """F-SOFTPAUSE-14: 4 min left: nothing goes to the timed lane; the weather stays soft
    and plays after the shiur in the music lane."""
    station, _, engine, _ = eng
    st, shiur, weather = await _engine_shiur(client, db_session, station, left=4 * 60)
    await playout_engine.tick(db_session, st)
    assert engine.timed() == []
    assert (await _fresh(db_session, weather.id)).timing_mode == "soft"


@pytest.mark.asyncio
async def test_engine_wait_station_keeps_soft_items_soft(client, db_session, eng):
    station, _, engine, _ = eng
    st, _, weather = await _engine_shiur(client, db_session, station, left=20 * 60, policy="wait")
    await playout_engine.tick(db_session, st)
    assert engine.timed() == []
    assert (await _fresh(db_session, weather.id)).timing_mode == "soft"


@pytest.mark.asyncio
async def test_soft_item_turns_hard_ahead_so_it_starts_on_time(db_session):
    """F-SOFTPAUSE-18: live on Jerusalem the weather was made hard only when due and
    started on the next 3 s poll (+2.3 s) with no pre-load on the page. It now turns
    hard 90 s ahead: the control room pre-loads it and the exact timer starts it."""
    station, talk, (weather,), _ = await _setup(db_session, left=20 * 60, due_ago=-60)
    on_air = await queue_module._check_advance(db_session, station.id)
    assert on_air.asset_id == talk.id  # not due yet: the shiur plays on
    row = next(e for e in await _pending(db_session, station.id) if e.asset_id == weather.id)
    assert row.timing_mode == "hard"


@pytest.mark.asyncio
async def test_soft_item_far_ahead_is_left_soft(db_session):
    """Two minutes ahead is beyond the look-ahead: nothing changes yet."""
    station, talk, (weather,), _ = await _setup(db_session, left=20 * 60, due_ago=-120)
    await queue_module._check_advance(db_session, station.id)
    row = next(e for e in await _pending(db_session, station.id) if e.asset_id == weather.id)
    assert row.timing_mode == "soft"


@pytest.mark.asyncio
async def test_ahead_rule_uses_time_left_when_due(db_session):
    """Six minutes left now but the item is due in 80 s: under 5 min left by then, so it waits."""
    station, talk, (weather,), _ = await _setup(db_session, left=6 * 60, due_ago=-80)
    await queue_module._check_advance(db_session, station.id)
    row = next(e for e in await _pending(db_session, station.id) if e.asset_id == weather.id)
    assert row.timing_mode == "soft"
