"""A station ID is passed over at air time when it would follow a break or another
spoken item, or land next to a break (services/station_id_guard.py). The fill only
predicts air times; a soft break waits for the song, so the simulator heard an ID
0.9 minutes after a :30 break (2026-11-19)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.queue import _pick_next_pending
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import playout_engine, station_id_guard


async def _setup(db: AsyncSession, before: str, break_in_minutes: float | None, song_left: float = 60.0,
                 source: str = "auto"):
    """On air: `before` ("song", "break" or "id"); then an ID, then a song; maybe a
    soft break due `break_in_minutes` from now."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True}})
    db.add(station)
    await db.flush()

    def asset(kind, category, seconds):
        a = Asset(id=uuid.uuid4(), title=f"{kind} {category}", file_path=f"{uuid.uuid4().hex}.mp3",
                  asset_type=kind, category=category, duration=seconds)
        db.add(a)
        return a

    song = asset("music", "lively", 200.0)
    after = asset("music", "lively", 210.0)
    sponsor = asset("spot", "sponsorship", 30.0)
    jingle = asset("jingle", "station_id", 8.0)
    jingle2 = asset("jingle", "station_id", 8.0)
    await db.flush()
    on_air = {"song": song, "break": sponsor, "id": jingle2}[before]
    started = now - timedelta(seconds=on_air.duration - song_left) if before == "song" else now - timedelta(seconds=5)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=on_air.id, position=1,
                      status="playing", started_at=started,
                      preempt_at=now - timedelta(minutes=1) if before == "break" else None,
                      timing_mode="soft" if before == "break" else None,
                      source="block" if before == "break" else "auto"))
    the_id = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=2,
                        status="pending", source=source)
    nxt = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=3,
                     status="pending", source="auto")
    db.add_all([the_id, nxt])
    if break_in_minutes is not None:
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=sponsor.id, position=4,
                          status="pending", source="block", timing_mode="soft",
                          preempt_at=now + timedelta(minutes=break_in_minutes)))
    await db.commit()
    return station.id, the_id.id, nxt.id


async def _status(db, entry_id):
    db.expire_all()
    return (await db.execute(select(QueueEntry.status).where(QueueEntry.id == entry_id))).scalar()


async def _moved_after(db, entry_id, song_id) -> bool:
    """The ID still waits, one song later (station_id_guard.postpone)."""
    db.expire_all()
    rows = dict((await db.execute(select(QueueEntry.id, QueueEntry.position).where(
        QueueEntry.id.in_([entry_id, song_id])))).all())
    return await _status(db, entry_id) == "pending" and rows[entry_id] > rows[song_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("before,break_in,song_left,airs", [
    ("song", None, 60.0, True),     # between songs, no break near: airs
    ("song", 20.0, 60.0, True),     # break well ahead: airs
    ("break", None, 60.0, False),   # right after a break: never spoken next to spoken
    ("id", None, 60.0, False),      # never two IDs
    ("song", 3.0, 60.0, False),     # the song ends in 1 min, a break 2 min after the ID
    ("song", 6.0, 200.0, False),    # the song has 3.3 min left: the ID would start 2.7 min before the break
])
async def test_browser_player_pick(db_session: AsyncSession, before, break_in, song_left, airs):
    station_id, id_entry, song_entry = await _setup(db_session, before, break_in, song_left)
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    await db_session.commit()
    if airs:
        assert picked.id == id_entry
    else:
        assert picked.id == song_entry
        assert await _moved_after(db_session, id_entry, song_entry)


@pytest.mark.asyncio
async def test_an_operators_id_is_never_second_guessed(db_session: AsyncSession):
    station_id, id_entry, _ = await _setup(db_session, "break", None, source="manual")
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    assert picked.id == id_entry


@pytest.mark.asyncio
async def test_engine_pick_passes_over_and_the_start_report_moves_it_one_song_later(db_session: AsyncSession):
    station_id, id_entry, song_entry = await _setup(db_session, "break", None)
    picked = await playout_engine.pick_next(db_session, station_id)
    assert picked.id == song_entry
    assert await _status(db_session, id_entry) == "pending"  # the pick only reads
    # asked again (the tick's check) it stays passed over
    assert (await playout_engine.pick_next(db_session, station_id)).id == song_entry
    assert str(id_entry) in station_id_guard.passed(station_id)
    assert await station_id_guard.take_passed(db_session, station_id) == 1
    await db_session.commit()
    assert await _moved_after(db_session, id_entry, song_entry)
    assert not station_id_guard.passed(station_id)


@pytest.mark.asyncio
async def test_engine_pick_between_songs_gives_the_id(db_session: AsyncSession):
    station_id, id_entry, _ = await _setup(db_session, "song", 20.0)
    assert (await playout_engine.pick_next(db_session, station_id)).id == id_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("handed_kind,airs", [("id", False), ("music", True)])
async def test_engine_never_gives_an_id_right_after_one_it_already_holds(db_session: AsyncSession, handed_kind, airs):
    """Main 2026-11-19 13:05Z, after an engine restart: two IDs back to back. radio.liq asks
    for the next item while a short one is still loading, before its start report, so the
    check must look at the item handed out, not at the song before it."""
    station_id, id_entry, song_entry = await _setup(db_session, "song", None)
    held = Asset(id=uuid.uuid4(), title="held", file_path=f"{uuid.uuid4().hex}.mp3", asset_type=
                 "jingle" if handed_kind == "id" else "music",
                 category="station_id" if handed_kind == "id" else "lively",
                 duration=9.0 if handed_kind == "id" else 200.0)
    db_session.add(held)
    await db_session.flush()
    handed = QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=held.id, position=2,
                        status="pending", source="auto")
    db_session.add(handed)
    await db_session.commit()
    handed_id = str(handed.id)
    picked = await playout_engine.pick_next(db_session, station_id, {handed_id}, handed_id)
    assert picked.id == (id_entry if airs else song_entry)
    station_id_guard._passed.pop(str(station_id), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes_ago,airs", [(10, False), (13, True)])
async def test_not_sooner_than_the_shortest_gap_after_the_last_id(db_session: AsyncSession, minutes_ago, airs):
    """Simulator: IDs 10.2 min apart after a hard :30 cut the song between them."""
    station_id, id_entry, song_entry = await _setup(db_session, "song", None)
    jingle = (await db_session.execute(select(Asset).where(Asset.category == "station_id"))).scalars().first()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=jingle.id, position=0,
                              status="played", source="auto",
                              started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))
    await db_session.commit()
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    assert picked.id == (id_entry if airs else song_entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds,airs", [(1.2, False), (1.8, True)])
async def test_a_nearly_empty_id_never_airs_or_gets_queued(db_session: AsyncSession, seconds, airs):
    """Floor 1.5 s: the parked IDs (1.78 to 2.32 s) air again since the engine fetches
    ahead during short items; a nearly empty file never does."""
    from app.services.playlist_generator_service import _Library

    station_id, id_entry, song_entry = await _setup(db_session, "song", None)
    jingle = (await db_session.execute(
        select(Asset).join(QueueEntry, QueueEntry.asset_id == Asset.id).where(QueueEntry.id == id_entry))).scalar_one()
    jingle.duration = seconds
    await db_session.commit()
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    assert picked.id == (id_entry if airs else song_entry)
    pool = await _Library(db_session, station_id, set()).pool("jingle", "station_id")
    assert pool and all(r.duration >= 1.5 for r in pool)
    assert any(r.id == jingle.id for r in pool) == airs


@pytest.mark.asyncio
@pytest.mark.parametrize("break_started_minutes_ago,airs", [(2.0, False), (5.0, True)])
async def test_a_short_song_after_a_break_is_not_enough(db_session: AsyncSession, break_started_minutes_ago, airs):
    """Simulator: break at 17:01:16, a short song, then an ID at 17:04:03 (2.8 min)."""
    station_id, id_entry, song_entry = await _setup(db_session, "song", None, song_left=10.0)
    spot = (await db_session.execute(select(Asset).where(Asset.category == "sponsorship"))).scalars().first()
    at = datetime.now(UTC) - timedelta(minutes=break_started_minutes_ago)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=spot.id, position=0,
                              status="played", source="block", timing_mode="soft", preempt_at=at, started_at=at))
    await db_session.commit()
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    assert picked.id == (id_entry if airs else song_entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("sponsor_status", ["playing", "played"])
async def test_no_id_after_a_sponsorship_that_waited_for_a_story(db_session: AsyncSession, sponsor_status):
    """All Day Stories 2026-11-19 06:36Z, browser player: TEST SPONSOR 08 waited for a story
    to end (sponsorships_wait_for_talks), aired at 06:36:04, and an ID followed at 06:36:32."""
    station_id, id_entry, song_entry = await _setup(db_session, "song", None)
    now = datetime.now(UTC)
    story = Asset(id=uuid.uuid4(), title="A story", file_path=f"{uuid.uuid4().hex}.mp3",
                  asset_type="story", category="stories", duration=1200.0)
    db_session.add(story)
    await db_session.flush()
    spot = (await db_session.execute(select(Asset).where(Asset.category == "sponsorship"))).scalars().first()
    # the song in _setup aired before the story; the story then the late sponsorship
    for e in (await db_session.execute(select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "playing"))).scalars():
        e.status, e.started_at = "played", now - timedelta(minutes=40)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=story.id, position=0,
                              status="played", source="auto", started_at=now - timedelta(minutes=20)))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=spot.id, position=1,
                              status=sponsor_status, source="ad_slot", timing_mode="soft",
                              preempt_at=now - timedelta(minutes=12), started_at=now - timedelta(seconds=28)))
    await db_session.commit()
    picked = await _pick_next_pending(db_session, station_id, now)
    await db_session.commit()
    assert picked.id == song_entry
    assert await _moved_after(db_session, id_entry, song_entry)


@pytest.mark.asyncio
async def test_queue_tidy_moves_ids_away_from_breaks_and_keeps_clear_ones(db_session: AsyncSession):
    """Main 2026-11-19: the queue showed an ID at 08:15:34 right after TEST SPONSOR 14
    and one at 07:57:45, 2.2 min before the 08:00 break. A waiting ID the queue's own
    timeline puts beside a break is taken out; one well clear stays."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True}})
    db_session.add(station)
    await db_session.flush()

    def asset(kind, category, seconds):
        a = Asset(id=uuid.uuid4(), title=f"{kind} {category}", file_path=f"{uuid.uuid4().hex}.mp3",
                  asset_type=kind, category=category, duration=seconds)
        db_session.add(a)
        return a

    song = asset("music", "lively", 243.0)  # 240 s on air (3 s crossfade)
    sponsor = asset("spot", "sponsorship", 30.0)
    jingle = asset("jingle", "station_id", 8.0)
    await db_session.flush()
    pos = 0

    def add(a, **kw):
        nonlocal pos
        pos += 1
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=pos,
                       status=kw.pop("status", "pending"), source=kw.pop("source", "auto"), **kw)
        db_session.add(e)
        return e

    add(song, status="playing", started_at=now)          # on air until +4 min
    songs = [add(song) for _ in range(2)]                 # +4, +8
    clear = add(jingle)                                   # +12: 8 min from the break, stays
    songs += [add(song) for _ in range(2)]                # +12:08, +16:08
    add(sponsor, source="ad_slot", timing_mode="soft",    # due +20, waits for the song: +20:08
        preempt_at=now + timedelta(minutes=20))
    after_break = add(jingle)                             # +20:38 right after the sponsorship
    songs += [add(song) for _ in range(2)]
    near = add(jingle)                                    # 2 min before the next break
    add(song)
    add(sponsor, source="ad_slot", timing_mode="soft",
        preempt_at=now + timedelta(minutes=30, seconds=50))
    also_after = add(jingle)                              # after the song, so right after that break too
    await db_session.commit()

    song_ids = [e.id for e in songs]
    ids = {k: e.id for k, e in (("clear", clear), ("after", after_break), ("near", near), ("also", also_after))}
    station_id = station.id
    dropped = await station_id_guard.drop_misplaced(db_session, station_id, now)
    await db_session.commit()
    db_session.expire_all()
    rows = (await db_session.execute(
        select(QueueEntry.id, QueueEntry.position).where(QueueEntry.station_id == station_id)
        .order_by(QueueEntry.position))).all()
    order = [r[0] for r in rows]
    assert ids["clear"] in order
    # moved one song later (2026-11-19: only removing them left a 55-minute gap on Main)
    assert ids["after"] in order
    assert order.index(ids["after"]) == order.index(song_ids[4]) + 1
    assert order.index(ids["after"]) == order.index(song_ids[5]) - 1
    # no room before the next ID's turn: left in place for the air-time check (removing
    # them here wore Main's IDs away)
    assert ids["near"] in order and ids["also"] in order
    assert dropped == 1


@pytest.mark.asyncio
async def test_an_id_that_cannot_move_before_the_next_id_is_skipped(db_session: AsyncSession):
    """Postponing stops at the next ID: two never play together."""
    station_id, id_entry, song_entry = await _setup(db_session, "break", None)
    jingle = (await db_session.execute(select(Asset).where(Asset.category == "station_id"))).scalars().first()
    song = (await db_session.execute(select(QueueEntry).where(QueueEntry.id == song_entry))).scalar_one()
    song.asset_id = jingle.id  # the item after this ID is another ID
    await db_session.commit()
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    await db_session.commit()
    assert picked is None or picked.id != id_entry
    assert await _status(db_session, id_entry) == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("last_aired_minutes_ago,after_song", [(10.0, 0), (2.0, 1)])
async def test_a_late_id_is_brought_forward_to_keep_them_12_to_20_minutes_apart(
        db_session: AsyncSession, last_aired_minutes_ago, after_song):
    """Main 2026-11-19 17:10-18:10Z: IDs 28 and 31 minutes apart. An ID passed over or moved
    goes one song later and nothing brought it back. When the queue's timeline puts the next
    ID more than max_minutes (20) after the last one aired, it moves to the first place at
    least min_minutes (12) after it that the air-time check accepts."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True, "clear_minutes": 1}})
    db_session.add(station)
    await db_session.flush()

    def asset(kind, category, seconds):
        a = Asset(id=uuid.uuid4(), title=f"{kind} {category}", file_path=f"{uuid.uuid4().hex}.mp3",
                  asset_type=kind, category=category, duration=seconds)
        db_session.add(a)
        return a

    song = asset("music", "lively", 243.0)  # 240 s on air
    jingle = asset("jingle", "station_id", 8.0)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=0,
                              status="played", source="auto",
                              started_at=now - timedelta(minutes=last_aired_minutes_ago)))
    pos = 0

    def add(a, **kw):
        nonlocal pos
        pos += 1
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=pos,
                       status=kw.pop("status", "pending"), source="auto", **kw)
        db_session.add(e)
        return e

    add(song, status="playing", started_at=now)   # on air until +4
    songs = [add(song) for _ in range(5)]          # +4, +8, +12, +16, +20
    late = add(jingle)                             # +24: 34 or 26 min after the last
    add(song)
    await db_session.commit()
    station_id, late_id, song_ids = station.id, late.id, [s.id for s in songs]

    moved = await station_id_guard.pull_late_id(db_session, station_id, now)
    await db_session.commit()
    db_session.expire_all()
    order = [r[0] for r in (await db_session.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position))).all()]
    # the first song boundary 12+ min after the last ID that leaves the engine's next item
    # alone: 10 min ago -> +8 (18 min, after song 0); 2 min ago -> +12 (14 min, after song 1)
    assert moved == 1
    assert order.index(late_id) == order.index(song_ids[after_song]) + 1


@pytest.mark.asyncio
async def test_an_id_on_time_is_left_where_the_fill_put_it(db_session: AsyncSession):
    """An ID the timeline puts within max_minutes of the last one stays put."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True, "clear_minutes": 1}})
    db_session.add(station)
    await db_session.flush()
    song = Asset(id=uuid.uuid4(), title="s", file_path="s.mp3", asset_type="music", category="lively", duration=243.0)
    jingle = Asset(id=uuid.uuid4(), title="j", file_path="j.mp3", asset_type="jingle", category="station_id",
                   duration=8.0)
    db_session.add_all([song, jingle])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=0,
                              status="played", source="auto", started_at=now - timedelta(minutes=2)))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                              status="playing", source="auto", started_at=now))
    for p in range(2, 5):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=p,
                                  status="pending", source="auto"))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=5,
                              status="pending", source="auto"))   # +16: 18 min after the last
    await db_session.commit()
    assert await station_id_guard.pull_late_id(db_session, station.id, now) == 0


@pytest.mark.asyncio
async def test_an_id_waiting_far_too_close_after_the_one_before_is_taken_out(db_session: AsyncSession):
    """Main 2026-11-19 22:50Z: an ID passed over since 17:49 waited at 22:54:30 and the
    redraw's next one at 23:03:57, 9.5 min after it; the second would be passed over and
    push the ones behind it later. The later one is taken out; the first stays."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True, "clear_minutes": 1}})
    db_session.add(station)
    await db_session.flush()
    song = Asset(id=uuid.uuid4(), title="s", file_path="s.mp3", asset_type="music", category="lively", duration=243.0)
    jingle = Asset(id=uuid.uuid4(), title="j", file_path="j.mp3", asset_type="jingle", category="station_id",
                   duration=8.0)
    db_session.add_all([song, jingle])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=0,
                              status="played", source="auto", started_at=now - timedelta(minutes=9)))
    pos = 0

    def add(a, **kw):
        nonlocal pos
        pos += 1
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=pos,
                       status=kw.pop("status", "pending"), source="auto", **kw)
        db_session.add(e)
        return e

    add(song, status="playing", started_at=now)   # on air until +4
    first = add(jingle)                            # +4: 13 min after the last aired
    add(song)
    add(song)                                      # +4, +8
    close = add(jingle)                            # +12: 8 min after the first
    for _ in range(3):
        add(song)
    await db_session.commit()
    station_id, first_id, close_id = station.id, first.id, close.id

    changed = await station_id_guard.pull_late_id(db_session, station_id, now)
    await db_session.commit()
    db_session.expire_all()
    assert changed == 1
    assert await _status(db_session, close_id) == "skipped"
    assert await _status(db_session, first_id) == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("last_aired_minutes_ago,waits", [(14.0, True), (25.0, False)])
async def test_an_id_already_late_keeps_only_a_minute_from_breaks(
        db_session: AsyncSession, last_aired_minutes_ago, waits):
    """Main 2026-11-19 23:36Z: an ID due 2 min after a late sponsorship was passed over
    song by song for 32 min. Within max_minutes it keeps clear_minutes (3) from breaks;
    once later than that, only LATE_MARGIN (1)."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True}})
    db_session.add(station)
    await db_session.flush()
    song = Asset(id=uuid.uuid4(), title="s", file_path="s.mp3", asset_type="music", category="lively", duration=100.0)
    spot = Asset(id=uuid.uuid4(), title="sp", file_path="sp.mp3", asset_type="spot", category="Spots", duration=26.0)
    jingle = Asset(id=uuid.uuid4(), title="j", file_path="j.mp3", asset_type="jingle", category="station_id",
                   duration=8.0)
    db_session.add_all([song, spot, jingle])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=0,
                              status="played", source="auto",
                              started_at=now - timedelta(minutes=last_aired_minutes_ago)))
    # a soft sponsorship aired 2 min ago, then a song that ends now
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=1,
                              status="played", source="block", preempt_at=now - timedelta(minutes=5),
                              started_at=now - timedelta(minutes=2)))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                              status="playing", source="auto", started_at=now - timedelta(seconds=97)))
    entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=3,
                       status="pending", source="auto")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry, ["asset"])

    assert await station_id_guard.must_wait(db_session, station.id, entry, now) is waits
