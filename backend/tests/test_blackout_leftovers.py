"""What was queued for before a blackout never airs after it.

Queue read 2026-11-19 21:22Z: Main's Friday-afternoon block and a sponsorship timed for
21:15 Friday were lined up to air first on Motzei Shabbos, Jerusalem's Erev Shabbos daf
too, and Main's TEST SPONSOR 29 was due 7 s before the silence (cut mid-spot).
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_replenish(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)


def _asset(db, title, kind, duration):
    a = Asset(id=uuid.uuid4(), title=title, duration=float(duration), asset_type=kind,
              file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(a)
    return a


def _row(station, asset, position, **kw):
    return QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=position,
                      status=kw.pop("status", "pending"), source=kw.pop("source", "auto"), **kw)


async def _status(db, station):
    return {
        e.id: e.status for e in (await db.execute(
            select(QueueEntry).where(QueueEntry.station_id == station.id)
            .execution_options(populate_existing=True))).scalars().all()
    }


@pytest.mark.asyncio
async def test_rows_queued_before_the_silences_leave_once_the_blackout_begins(db_session):
    station = Station(id=uuid.uuid4(), name="Kol B'ramah Main", timezone="America/New_York")
    db_session.add(station)
    silence = _asset(db_session, "Silence (5 min)", "silence", 300)
    song = _asset(db_session, "Rozo Deshabbos", "music", 240)
    daf = _asset(db_session, "Yoma 9", "shiur", 3060)
    sponsor = _asset(db_session, "TEST SPONSOR 50", "spot", 30)
    opening = _asset(db_session, "Motzei Shabbos opening", "music", 200)
    shofar = _asset(db_session, "Shofar", "jingle", 20)
    await db_session.flush()
    now = datetime.now(UTC)
    start, end = now - timedelta(minutes=10), now + timedelta(hours=24)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=end, is_blackout=True, affected_stations=None))
    # Friday afternoon's leftovers, ahead of the silences
    left = [_row(station, song, 1), _row(station, daf, 2)]
    timed_before = _row(station, sponsor, 3, source="block", timing_mode="hard",
                        preempt_at=start - timedelta(minutes=3))
    at_lighting = _row(station, shofar, 4, source="shabbos_end", timing_mode="hard", preempt_at=start)
    # a timed item for after the window, even one ahead of the silences, stays
    after_timed = _row(station, opening, 5, source="block", timing_mode="hard",
                       preempt_at=end + timedelta(minutes=5))
    n = int((end - now).total_seconds() // 300) + 2
    silences = [_row(station, silence, 10 + i, timing_mode="hard", preempt_at=start) for i in range(n)]
    after = _row(station, opening, 10 + n + 1)
    db_session.add_all([*left, timed_before, at_lighting, after_timed, *silences, after])
    await db_session.commit()

    await queue_module.fill_blackout_queue(db_session, station.id)

    status = await _status(db_session, station)
    assert [status[r.id] for r in left] == ["skipped", "skipped"]
    assert status[timed_before.id] == "skipped"
    assert status[at_lighting.id] == "pending"
    assert status[after_timed.id] == "pending"
    assert status[after.id] == "pending"
    assert all(status[s.id] == "pending" for s in silences)


@pytest.mark.asyncio
async def test_nothing_is_dropped_before_the_blackout_begins(db_session):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(station)
    _asset(db_session, "Silence (5 min)", "silence", 300)
    song = _asset(db_session, "Menucha Vsimcha", "music", 240)
    await db_session.flush()
    now = datetime.now(UTC)
    start, end = now + timedelta(hours=3), now + timedelta(hours=28)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=end, is_blackout=True, affected_stations=None))
    rows = [_row(station, song, i + 1) for i in range(5)]
    db_session.add_all(rows)
    await db_session.commit()

    await queue_module.fill_blackout_queue(db_session, station.id)

    status = await _status(db_session, station)
    assert all(status[r.id] == "pending" for r in rows)


@pytest.mark.asyncio
async def test_a_spot_the_silence_would_cut_is_not_started(db_session):
    station = Station(id=uuid.uuid4(), name="Kol B'ramah Main", timezone="America/New_York")
    db_session.add(station)
    _asset(db_session, "Silence (5 min)", "silence", 300)
    long_song = _asset(db_session, "Toda", "music", 590)
    jingle = _asset(db_session, "Station ID", "jingle", 5)
    spot = _asset(db_session, "TEST SPONSOR 29", "spot", 29)
    song = _asset(db_session, "Sameach", "music", 200)
    await db_session.flush()
    now = datetime.now(UTC)
    start, end = now + timedelta(minutes=10), now + timedelta(hours=25)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=end, is_blackout=True, affected_stations=None))
    on_air = _row(station, long_song, 1, status="playing", started_at=now)  # ends 10 s before
    fits = _row(station, jingle, 2)  # 5 s: ends 5 s before the window
    cut = _row(station, spot, 3)  # would run 24 s into the silence
    music = _row(station, song, 4)  # a song just fades, as at the hour
    db_session.add_all([on_air, fits, cut, music])
    await db_session.commit()

    await queue_module.fill_blackout_queue(db_session, station.id)

    status = await _status(db_session, station)
    assert status[fits.id] == "pending"
    assert status[cut.id] == "skipped"
    assert status[music.id] == "pending"


@pytest.mark.asyncio
async def test_the_track_the_engine_holds_for_after_the_silence_is_dropped(db_session, monkeypatch):
    """Simulator, Jerusalem 2026-11-20: the engine was handed its next song at 13:54:18Z,
    the silence started at 13:57:14Z over it, and the song stayed loaded through Shabbos
    to air first after it. It sat after the silences, where rows drawn for after the
    window also sit, so only the engine's own hold tells it apart."""
    from app.config import settings
    from app.services import playout_engine

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(station)
    silence = _asset(db_session, "Silence (5 min)", "silence", 300)
    song = _asset(db_session, "Lecho Dodi", "music", 240)
    opening = _asset(db_session, "Motzei Shabbos opening", "music", 200)
    await db_session.flush()
    now = datetime.now(UTC)
    start, end = now - timedelta(minutes=1), now + timedelta(hours=24)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=end, is_blackout=True, affected_stations=None))
    n = int((end - now).total_seconds() // 300) + 2
    silences = [_row(station, silence, 1 + i, timing_mode="hard", preempt_at=start) for i in range(n)]
    cued = _row(station, song, n + 5)
    after = _row(station, opening, n + 6)
    db_session.add_all([*silences, cued, after])
    await db_session.commit()

    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: str(sid) == str(station.id))
    flushed = []

    async def flush(sid):
        flushed.append(str(sid))
        return True

    monkeypatch.setattr(playout_engine, "engine_flush", flush)
    state = playout_engine.get_state(station.id)
    state.cued_entry_id = str(cued.id)
    try:
        await queue_module.fill_blackout_queue(db_session, station.id)
    finally:
        state.cued_entry_id = None

    status = await _status(db_session, station)
    assert status[cued.id] == "skipped"
    assert status[after.id] == "pending"
    assert flushed == [str(station.id)]


@pytest.mark.asyncio
async def test_in_a_blackout_the_engine_is_handed_no_song(db_session):
    """Only the blackout's silence goes to the engine while it lasts; a song handed then
    would wait loaded until the silence ends and air first after Shabbos."""
    from app.services import playout_engine

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(station)
    song = _asset(db_session, "Lecho Dodi", "music", 240)
    await db_session.flush()
    now = datetime.now(UTC)
    window = HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=now - timedelta(minutes=5),
                           end_datetime=now + timedelta(hours=24), is_blackout=True, affected_stations=None)
    db_session.add_all([window, _row(station, song, 1)])
    await db_session.commit()
    playout_engine.reset_state(station.id)

    assert await playout_engine.handle_next(db_session, station.id) is None

    window.end_datetime = now - timedelta(minutes=1)  # Shabbos is over
    await db_session.commit()
    nxt = await playout_engine.handle_next(db_session, station.id)
    assert nxt is not None and nxt["uri"]
    playout_engine.reset_state(station.id)


@pytest.mark.asyncio
async def test_the_shofar_still_goes_to_the_engine_in_the_blackout(db_session):
    """The item at candle lighting is first in the silence group: normally in the timed
    lane already, but an engine restart that minute asks for it with /next."""
    from app.services import playout_engine

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(station)
    shofar = _asset(db_session, "Shofar", "jingle", 20)
    await db_session.flush()
    now = datetime.now(UTC)
    start = now - timedelta(seconds=20)
    db_session.add_all([
        HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                      end_datetime=now + timedelta(hours=24), is_blackout=True, affected_stations=None),
        _row(station, shofar, 1, source="shabbos_end", timing_mode="hard", preempt_at=start),
    ])
    await db_session.commit()
    playout_engine.reset_state(station.id)
    nxt = await playout_engine.handle_next(db_session, station.id)
    playout_engine.reset_state(station.id)
    assert nxt is not None and nxt["uri"]
