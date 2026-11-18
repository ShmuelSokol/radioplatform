"""Station IDs between songs, edges found while building the test list (checklist
V3-69, V3-71): the spacing counts from an ID that already aired, not only from one
waiting in the queue (a redraw after a schedule change empties the queue of IDs),
and one station's setting never adds IDs to another station."""
import random
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.last_played as lp
from app.services.playlist_generator_service import generate
from tests.test_playlist_generation import MONDAY_7AM, _setup
from tests.test_station_ids_between_songs import IDS, _jingle, _station_with_ids


@pytest.mark.asyncio
async def test_an_id_that_just_aired_holds_off_the_next(db_session: AsyncSession, monkeypatch):
    """An ID aired two minutes before the fill starts, none is queued (a redraw
    after a schedule change, or Next through the queue): the first new ID comes
    at least 12 minutes after the one that aired, give or take one song."""
    station, jingles = await _station_with_ids(db_session, IDS, jingles=3)
    for seed in range(8):
        random.seed(seed)
        aired = MONDAY_7AM - timedelta(minutes=2)

        async def history(db, station_id, _aired=aired, _id=str(jingles[0].id)):
            return {_id: _aired}

        monkeypatch.setattr(lp, "last_played", history)
        plan = await generate(db_session, station, MONDAY_7AM, 1, artist_distance=0)
        first = next((p for p in plan.picks if p.asset_type == "jingle"), None)
        assert first is not None
        assert first.at - aired >= timedelta(minutes=12) - timedelta(seconds=240), (seed, first.at - aired)
        assert first.asset_id != jingles[0].id  # rotation: not the one that just aired


@pytest.mark.asyncio
async def test_ids_on_one_station_add_none_to_another(db_session: AsyncSession):
    random.seed(4)
    with_ids, jingles = await _station_with_ids(db_session, IDS)
    with_ids.name = "KBR Jerusalem"
    await db_session.commit()
    without, *_ = await _setup(db_session, None)
    plan = await generate(db_session, without, MONDAY_7AM, 3, artist_distance=0)
    assert plan.picks
    assert all(p.asset_type == "music" for p in plan.picks)
    ids_plan = await generate(db_session, with_ids, MONDAY_7AM, 3, artist_distance=0)
    assert any(p.asset_type == "jingle" for p in ids_plan.picks)


@pytest.mark.asyncio
async def test_an_id_category_of_another_station_is_not_drawn(db_session: AsyncSession):
    """A station that names its own ID category draws only from it, never the
    default "station_id" jingles another station uses."""
    random.seed(6)
    station, default_ids = await _station_with_ids(db_session, {**IDS, "category": "jlm_id"})
    own = [_jingle(n, "jlm_id") for n in range(3)]
    db_session.add_all(own)
    await db_session.commit()
    plan = await generate(db_session, station, MONDAY_7AM, 2, artist_distance=0)
    drawn = {p.asset_id for p in plan.picks if p.asset_type == "jingle"}
    assert drawn and drawn <= {j.id for j in own}
