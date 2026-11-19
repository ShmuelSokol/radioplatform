"""A start report and a next ask that overlap (Main, 2026-11-19 16:50Z).

The engine reported a song on air; the report took 751 ms. Meanwhile it asked for the
item after a 4 s item, naming the song in `given`. The song still showed as pending, so
the next call closed it as "aired without a start report reaching us", and the site
showed nothing on air for 6 minutes while the stream played it.
"""
import asyncio

import pytest

from app.services import playout_engine
from tests import test_engine_backend as _engine_tests
from tests.test_engine_backend import _fresh, _next, _on_air

eng = _engine_tests.eng


@pytest.mark.asyncio
async def test_a_next_ask_during_a_start_report_does_not_close_the_song(client, db_session, eng):
    station, entries, _, _ = eng
    song, short, after = entries[0], entries[1], entries[2]
    assert (await _next(client, station)).json()["entry_id"] == str(song.id)
    assert (await _next(client, station, given=str(song.id))).json()["entry_id"] == str(short.id)

    # The song's start report is in flight: it waits behind the lock
    state = playout_engine.get_state(station.id)
    await state.started_lock.acquire()
    report = asyncio.create_task(_on_air(client, station, song.id))
    await asyncio.sleep(0.05)
    try:
        r = await _next(client, station, given=f"{short.id},{song.id}")
        assert r.json()["entry_id"] == str(after.id)
        assert (await _fresh(db_session, song.id)).status == "pending"  # not closed
    finally:
        state.started_lock.release()
    await report
    assert (await _fresh(db_session, song.id)).status == "playing"


@pytest.mark.asyncio
async def test_an_entry_whose_report_never_came_is_still_closed(client, db_session, eng):
    station, entries, _, _ = eng
    song, short = entries[0], entries[1]
    await _next(client, station)
    await _next(client, station, given=str(song.id))
    await _next(client, station, given=f"{short.id},{song.id}")
    assert (await _fresh(db_session, song.id)).status == "played"
