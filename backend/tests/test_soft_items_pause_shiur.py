"""Soft timed items (the :30 weather, sponsorships) during a shiur or story.

KBR Jerusalem's 7:30 weather aired at 7:49 behind a 49-minute shiur. Shmuel's rule
(2026-11-17): if the shiur has more than 5 minutes left, pause it; under 5 minutes,
wait. Checklist rows F-HANDOVER-21 and F-SOFTPAUSE-* (docs/test-checklist.md).
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

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
                 soft_source=None, due_ago=1.0, hard_by_in=None, talk_preempt_at=None,
                 extra_config=None, soft_mode=None):
    """A shiur on air with `left` seconds to go and a soft group due `due_ago` s ago."""
    config = {"timed_during_long_items": policy} if policy else {}
    config.update(extra_config or {})
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
                       timing_mode=soft_mode or (None if soft_source == "ad_slot" else "soft"),
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
async def test_turning_hard_ahead_is_saved(db_session):
    """F-SOFTPAUSE-20: live on Jerusalem 2026-11-17 the weather read soft until it
    started, because the 3 s poll rolls its session back after a check that changes
    nothing on air. The flip must be committed so pages see it 90 s ahead."""
    station, talk, (weather,), _ = await _setup(db_session, left=20 * 60, due_ago=-60)
    station_id, weather_id = station.id, weather.id
    await queue_module._check_advance(db_session, station_id)
    await db_session.rollback()  # what the scheduler does after every tick
    rows = [e for e in await _pending(db_session, station_id) if e.asset_id == weather_id]
    assert rows and rows[0].timing_mode == "hard"


@pytest.mark.asyncio
async def test_wait_station_softening_is_saved(db_session):
    """F-SOFTPAUSE-21: on a "wait" station a hard time check due during the shiur is
    made soft. That too was lost to the poll's rollback, so pages kept it hard and
    could fade the shiur themselves at the hour."""
    station, talk, (clock,), _ = await _setup(db_session, left=20 * 60, policy="wait", due_ago=-30)
    station_id, clock_id = station.id, clock.id
    await db_session.execute(update(QueueEntry).where(QueueEntry.asset_id == clock_id)
                             .values(timing_mode="hard", source="timed"))
    await db_session.commit()
    await queue_module._check_advance(db_session, station_id)
    await db_session.rollback()
    rows = [e for e in await _pending(db_session, station_id) if e.asset_id == clock_id]
    assert rows and rows[0].timing_mode == "soft"


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


# ── The queue's shown times ──────────────────────────────────────────────────


def _shown(body, title):
    row = next(r for r in body["entries"] if (r.get("asset") or {}).get("title") == title)
    return datetime.fromisoformat(row["estimated_start"])


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,soft_wait,song_wait", [
    # each item holds the air to its crossfade point, 3 s before its end (on_air_seconds)
    (None, 600, 40 * 60 + 30 - 6),      # pause: the item airs on time, the shiur ends 30 s later
    ("wait", 40 * 60, 40 * 60 + 30 - 6),  # wait: it follows the shiur
])
async def test_queue_shows_a_soft_item_at_the_time_it_will_pause_the_shiur(
        client, auth_headers, db_session, policy, soft_wait, song_wait):
    """Main's 8:30 PM sponsorship was shown at 9:00:45, behind the whole Daf Yomi,
    though it airs at 8:30 by pausing it."""
    station, _talk, _softs, _song = await _setup(db_session, left=40 * 60, policy=policy, due_ago=-600)
    now = datetime.now(UTC)
    r = await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert abs((_shown(body, "Soft 0") - now).total_seconds() - soft_wait) < 5
    assert abs((_shown(body, "Song") - now).total_seconds() - song_wait) < 5


@pytest.mark.asyncio
async def test_queue_shows_a_soft_item_after_a_shiur_that_is_nearly_over(client, auth_headers, db_session):
    station, *_ = await _setup(db_session, left=8 * 60, due_ago=-300)  # 3 min left when due
    now = datetime.now(UTC)
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert abs((_shown(body, "Soft 0") - now).total_seconds() - 8 * 60) < 5


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "hard"])
async def test_sponsorships_wait_for_the_story_on_a_station_that_says_so(db_session, mode):
    """All Day Stories (coordinator 2026-11-19): sponsorships alone never cut into a
    story there, soft or hard; they play when it ends, then the music."""
    station, talk, spots, song = await _setup(
        db_session, left=19 * 60, kind="story", soft_items=2, soft_source="ad_slot", soft_mode=mode,
        extra_config={long_items.SPONSORSHIPS_WAIT: True},
    )
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id
    assert (await _finish_current(db_session, station.id)).asset_id == spots[0].id
    assert (await _finish_current(db_session, station.id)).asset_id == spots[1].id
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "hard"])
async def test_a_waiting_sponsorship_does_not_play_in_a_time_checks_pause_of_the_story(db_session, mode):
    """All Day Stories (sponsorships wait): a 10:45 sponsorship waits behind a story,
    the 11:00 time check pauses the story, and the story must carry on before the
    sponsorship plays, not after it (simulator, CI run of 2026-11-19 05:00Z)."""
    station, talk, softs, song = await _setup(
        db_session, left=30 * 60, kind="story", soft_source="ad_slot",
        extra_config={long_items.SPONSORSHIPS_WAIT: True}, soft_mode=mode,
    )
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id  # it waits
    # the 11:00 time check: hard, due now, pauses the story (resume queued after it)
    check = _asset(db_session, "The time is 11")
    await db_session.flush()
    await db_session.execute(
        update(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .values(position=QueueEntry.position + 1))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=2,
                              status="pending", source="block", timing_mode="hard",
                              preempt_at=datetime.now(UTC) - timedelta(seconds=0.5)))
    await db_session.commit()
    order = [(await queue_module._check_advance(db_session, station.id)).asset_id]
    for _ in range(3):
        order.append((await _finish_current(db_session, station.id)).asset_id)
    assert order == [check.id, talk.id, softs[0].id, song.id], order


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["soft", "hard"])
async def test_engine_plays_the_rest_of_a_paused_story_before_a_waiting_sponsorship(db_session, timing):
    """Same rule on the playout engine's pick (pick_next): after the 11:00 time check
    paused a story, a soft 10:45 sponsorship that waited for the story plays after the
    rest of it. The simulator drives All Day Stories through the engine and caught the
    sponsorship in the pause (CI on PR 428, 2026-11-19). A hard item still goes first."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={long_items.SPONSORSHIPS_WAIT: True})
    db_session.add(station)
    story = Asset(id=uuid.uuid4(), title="story", file_path="s.mp3", asset_type="story", duration=1800.0)
    spot = Asset(id=uuid.uuid4(), title="TEST SPONSOR", file_path="p.mp3", asset_type="spot", duration=30.0)
    song = Asset(id=uuid.uuid4(), title="song", file_path="m.mp3", asset_type="music", duration=200.0)
    db_session.add_all([story, spot, song])
    await db_session.flush()
    rest = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=story.id, position=1,
                      status="pending", source="resume@600")
    waiting = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=2,
                         status="pending", source="ad_slot", timing_mode=timing,
                         preempt_at=now - timedelta(minutes=15))
    db_session.add_all([rest, waiting, QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id,
                                                  position=3, status="pending", source="auto")])
    await db_session.commit()
    picked = await playout_engine.pick_next(db_session, station.id)
    assert picked.id == (rest.id if timing == "soft" else waiting.id)
    # once the rest of the story is handed out, the sponsorship comes next
    if timing == "soft":
        assert (await playout_engine.pick_next(db_session, station.id, {str(rest.id)})).id == waiting.id


@pytest.mark.asyncio
async def test_two_waiting_breaks_with_the_same_sponsorship_never_play_back_to_back(db_session):
    """All Day Stories: the 12:00 and 12:30 breaks both carried Bagel Nosh and both
    waited behind a story; when it ended they played 21 s apart (simulator, CI on
    PR 460). The second is skipped (the make-up places it again later)."""
    station, talk, (spot,), song = await _setup(
        db_session, left=19 * 60, kind="story", soft_source="ad_slot", due_ago=30 * 60,
        extra_config={long_items.SPONSORSHIPS_WAIT: True},
    )
    later = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=3,
                       status="pending", source="ad_slot", preempt_at=datetime.now(UTC) - timedelta(seconds=5))
    await db_session.execute(
        update(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending",
                                 QueueEntry.position >= 3)
        .values(position=QueueEntry.position + 1))
    db_session.add(later)
    await db_session.commit()
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id
    assert (await _finish_current(db_session, station.id)).asset_id == spot.id
    assert (await _finish_current(db_session, station.id)).asset_id == song.id
    await db_session.refresh(later)
    assert later.status == "skipped"


@pytest.mark.asyncio
async def test_engine_passes_over_a_sponsorship_that_just_aired(db_session):
    """Same rule on the engine path (read-only there): the second Bagel Nosh is passed
    over, the song plays."""
    station = Station(id=uuid.uuid4(), name="Kol B'ramah - All Day Stories", timezone="America/New_York",
                      automation_config={long_items.SPONSORSHIPS_WAIT: True})
    db_session.add(station)
    spot = _asset(db_session, "TEST SPONSOR 16 - Bagel Nosh", kind="spot")
    song = _asset(db_session, "Song", kind="music", duration=200)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=1,
                              status="playing", source="ad_slot", preempt_at=now - timedelta(minutes=40),
                              started_at=now - timedelta(seconds=21)))
    again = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=2,
                       status="pending", source="ad_slot", preempt_at=now - timedelta(minutes=10))
    db_session.add(again)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=3,
                              status="pending", source="auto"))
    await db_session.commit()
    picked = await playout_engine.pick_next(db_session, station.id)
    assert picked is not None and picked.asset_id == song.id
