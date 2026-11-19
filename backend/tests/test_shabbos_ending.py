"""The last song before Shabbos (services/shabbos_ending.py): back-timed to end at
candle lighting, the item after it first in the silence group, timed items cleared out
of its way, per-station setting. Clocks are pinned (Friday 2026-11-20)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station, StationType
from app.services import shabbos_ending

UTC = timezone.utc
CANDLES = datetime(2026, 11, 20, 21, 18, 23, tzinfo=UTC)  # Lakewood, 4:18:23 PM ET
NOW = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)


async def _setup(db, *, after=True, config_extra=None, reason="Shabbos", start=CANDLES):
    song = Asset(id=uuid.uuid4(), title="Time to Say Good Shabbos", asset_type="music", category="top_hits",
                 duration=279.48, file_path="s.mp3",
                 metadata_extra={"audio_analysis": {"version": 2, "cue_in_seconds": 0.23, "cue_out_seconds": 276.52}})
    shofar = Asset(id=uuid.uuid4(), title="Shofar", asset_type="jingle", category="shabbos_ending",
                   duration=20.0, file_path="f.mp3")
    silence = Asset(id=uuid.uuid4(), title="Silence (5 min)", asset_type="silence", duration=300.0, file_path="x.mp3")
    cfg = {"enabled": True, "song_asset_id": str(song.id), "after_asset_id": str(shofar.id) if after else None}
    cfg.update(config_extra or {})
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", type=StationType.INTERNET,
                      timezone="America/New_York", automation_config={"shabbos_ending": cfg})
    other = Station(id=uuid.uuid4(), name=f"O {uuid.uuid4().hex[:6]}", type=StationType.INTERNET,
                    timezone="America/New_York", automation_config={})
    db.add_all([song, shofar, silence, station, other])
    await db.flush()
    db.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                         end_datetime=start + timedelta(hours=25, minutes=30), is_blackout=True,
                         affected_stations={"station_ids": [str(station.id), str(other.id)]}, reason=reason))
    # The blackout's silence, as fill_blackout_queue queues it ahead
    for i in range(3):
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id, position=10 + i,
                          status="pending", preempt_at=start, source="auto"))
    await db.commit()
    return station, other, song, shofar, silence


async def _pending(db, station_id):
    res = await db.execute(select(QueueEntry).where(QueueEntry.station_id == station_id,
                                                    QueueEntry.status == "pending")
                           .order_by(QueueEntry.preempt_at, QueueEntry.position))
    return list(res.scalars().all())


def _at(e):
    return e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_song_ends_at_candle_lighting_and_shofar_leads_the_silence(db_session):
    station, other, song, shofar, silence = await _setup(db_session)
    await shabbos_ending.reconcile(db_session, station, NOW)
    rows = await _pending(db_session, station.id)
    mine = [e for e in rows if e.source == shabbos_ending.SOURCE]
    assert len(mine) == 2
    song_row = next(e for e in mine if e.asset_id == song.id)
    assert song_row.timing_mode == "hard"
    # played length = 276.52 - 0.23: its audible end lands on candle lighting
    assert abs((_at(song_row) - (CANDLES - timedelta(seconds=276.29))).total_seconds()) < 0.01
    group = [e for e in rows if _at(e) == CANDLES]
    assert group[0].asset_id == shofar.id, [e.asset_id for e in group]
    assert all(e.asset_id == silence.id for e in group[1:]) and len(group) == 4
    # The other station has no setting: nothing queued there
    assert await _pending(db_session, other.id) == []

    # A second pass changes nothing
    again = await shabbos_ending.reconcile(db_session, station, NOW + timedelta(seconds=3))
    assert again == {"added": 0, "removed": 0, "cleared": 0}
    assert len([e for e in await _pending(db_session, station.id) if e.source == shabbos_ending.SOURCE]) == 2


@pytest.mark.asyncio
async def test_without_shofar_only_the_song(db_session):
    station, _, song, _, _ = await _setup(db_session, after=False)
    await shabbos_ending.reconcile(db_session, station, NOW)
    mine = [e for e in await _pending(db_session, station.id) if e.source == shabbos_ending.SOURCE]
    assert [e.asset_id for e in mine] == [song.id]


@pytest.mark.asyncio
async def test_timed_items_cleared_from_the_songs_way(db_session):
    station, _, song, _, silence = await _setup(db_session)
    song_at = CANDLES - timedelta(seconds=276.29)
    filler = Asset(id=uuid.uuid4(), title="clip", asset_type="spot", duration=30.0, file_path="c.mp3")
    db_session.add(filler)
    await db_session.flush()

    def add(at, mode, source="block", pos=50):
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=filler.id, position=pos, status="pending",
                       preempt_at=at, timing_mode=mode, source=source)
        db_session.add(e)
        return e

    # A hard group starting 70 s before the song: its first two items fit, the third would run into the song
    g0 = song_at - timedelta(seconds=70)
    keep1, keep2, drop3 = add(g0, "hard", pos=1), add(g0, "hard", pos=2), add(g0, "hard", pos=3)
    inside = add(song_at + timedelta(seconds=60), "hard")          # a time check inside the song
    soft_late = add(CANDLES - timedelta(minutes=8), "soft")        # a break that would wait behind the song
    soft_early = add(song_at - timedelta(minutes=30), "soft")      # well before: left alone
    await db_session.commit()
    ids = {k: e.id for k, e in dict(keep1=keep1, keep2=keep2, drop3=drop3, inside=inside,
                                    soft_late=soft_late, soft_early=soft_early).items()}

    await shabbos_ending.reconcile(db_session, station, NOW)
    left = {e.id: e for e in await _pending(db_session, station.id)}
    assert ids["keep1"] in left and ids["keep2"] in left
    assert ids["drop3"] not in left and ids["inside"] not in left
    assert _at(left[ids["soft_late"]]) == song_at - shabbos_ending.SOFT_MOVE
    assert _at(left[ids["soft_early"]]) == song_at - timedelta(minutes=30)


@pytest.mark.asyncio
async def test_setting_off_removes_the_ending(db_session):
    station, _, _, _, _ = await _setup(db_session)
    await shabbos_ending.reconcile(db_session, station, NOW)
    station.automation_config = {"shabbos_ending": {**station.automation_config["shabbos_ending"], "enabled": False}}
    await db_session.commit()
    await shabbos_ending.reconcile(db_session, station, NOW + timedelta(minutes=1))
    assert [e for e in await _pending(db_session, station.id) if e.source == shabbos_ending.SOURCE] == []


@pytest.mark.asyncio
async def test_new_song_length_moves_the_start(db_session):
    """The song's analysis lands later (or is redone): the start follows it."""
    station, _, song, _, _ = await _setup(db_session)
    await shabbos_ending.reconcile(db_session, station, NOW)
    song.metadata_extra = {"audio_analysis": {"version": 2, "cue_in_seconds": 0.0, "cue_out_seconds": 270.0}}
    await db_session.commit()
    await shabbos_ending.reconcile(db_session, station, NOW + timedelta(minutes=1))
    rows = [e for e in await _pending(db_session, station.id)
            if e.source == shabbos_ending.SOURCE and e.asset_id == song.id]
    assert len(rows) == 1 and _at(rows[0]) == CANDLES - timedelta(seconds=270)


@pytest.mark.asyncio
async def test_left_alone_once_handed_to_the_engine(db_session):
    station, _, song, _, _ = await _setup(db_session)
    await shabbos_ending.reconcile(db_session, station, NOW)
    song.metadata_extra = {"audio_analysis": {"version": 2, "cue_in_seconds": 0.0, "cue_out_seconds": 270.0}}
    await db_session.commit()
    late = CANDLES - timedelta(seconds=276.29) - timedelta(seconds=30)
    await shabbos_ending.reconcile(db_session, station, late)
    rows = [e for e in await _pending(db_session, station.id)
            if e.source == shabbos_ending.SOURCE and e.asset_id == song.id]
    assert len(rows) == 1 and abs((_at(rows[0]) - (CANDLES - timedelta(seconds=276.29))).total_seconds()) < 0.01


@pytest.mark.asyncio
async def test_yom_tov_only_when_switched_on(db_session):
    # Erev Pesach on a Wednesday
    wed = datetime(2027, 4, 21, 23, 30, tzinfo=UTC)
    station, _, _, _, _ = await _setup(db_session, reason="Pesach (First Days)", start=wed)
    now = wed - timedelta(hours=2)
    await shabbos_ending.reconcile(db_session, station, now)
    assert [e for e in await _pending(db_session, station.id) if e.source == shabbos_ending.SOURCE] == []
    station.automation_config = {"shabbos_ending": {**station.automation_config["shabbos_ending"], "yom_tov": True}}
    await db_session.commit()
    await shabbos_ending.reconcile(db_session, station, now)
    assert len([e for e in await _pending(db_session, station.id) if e.source == shabbos_ending.SOURCE]) == 2


def test_window_kinds():
    fri = HolidayWindow(name="x", start_datetime=CANDLES, end_datetime=CANDLES, reason="Sukkot")
    assert shabbos_ending.window_kind(fri, "America/New_York") == {"shabbos", "yom_tov"}
    sh = HolidayWindow(name="x", start_datetime=CANDLES, end_datetime=CANDLES, reason="Shabbos")
    assert shabbos_ending.window_kind(sh, "America/New_York") == {"shabbos"}
    manual = HolidayWindow(name="x", start_datetime=CANDLES, end_datetime=CANDLES, reason="Manual")
    assert shabbos_ending.window_kind(manual, "America/New_York") == set()


def test_shabbos_ending_items_never_drawn_at_random():
    from app.models.asset import NEVER_RANDOM_CATEGORIES

    assert "shabbos_ending" in NEVER_RANDOM_CATEGORIES
