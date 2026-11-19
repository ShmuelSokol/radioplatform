"""Station simulator: a hand on the station at a chosen moment (tests/sim, wiki/station-simulator.md).

Each scenario runs the real scheduling code for a few minutes of air around the moment
that matters and changes something by hand on the way: the queue runs dry, the item on
air is deleted, a 15-minute song meets the time check, the season changes at midnight.
They take about half a minute each, so they run with SIM_LONG=1:

    cd backend && SIM_LONG=1 uv run pytest tests/test_station_sim_scenarios.py -v
"""
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, update

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from tests.sim.run import simulate
from tests.sim.stations import MAIN

NY = ZoneInfo("America/New_York")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
pytestmark = [
    pytest.mark.skipif(os.environ.get("SIM_LONG") != "1", reason="simulator scenarios: SIM_LONG=1"),
    pytest.mark.asyncio,
]
ON_TIME = ("hard items start on their second", "time check at :00 and :30 within 1 s",
           "time check says the right time", "no queue entry airs twice")
LOG = "site play log: one row per item aired, at its real time, none overlapping"
RESUME = "paused shiur: one resume per pause, no audio lost or repeated"


def at(h, m, s=0, day=19):
    return datetime(2026, 11, day, h, m, s, tzinfo=NY)


def failing(result, check):
    return [f"{f.at}: {f.text}" for f in result.failed(check)]


async def run(tmp_path, start, hours, events=None, blocks=None, overrides=None):
    _, result = await simulate(EXPORT, start, hours, ["main"], tmp_path, events=events, blocks=blocks,
                               overrides=overrides, quiet_pass=12.0)
    print(result.report())
    return result


async def test_hard_item_on_its_second_with_an_empty_queue(tmp_path):
    """V3-7: ten minutes before 8 PM every waiting song is removed and no music may be
    picked. Whatever fills the air meanwhile, the 8 PM time check starts on its second."""
    async def empty(sim):
        async with sim.Session() as db:
            await db.execute(update(Asset).where(Asset.asset_type == "music").values(category="do_not_play"))
            await db.execute(delete(QueueEntry).where(
                QueueEntry.station_id == uuid.UUID(MAIN), QueueEntry.status == "pending",
                QueueEntry.preempt_at.is_(None)))
            await db.commit()

    result = await run(tmp_path, at(19, 44), 0.33, events=[(at(19, 50), empty)])
    for check in ON_TIME:
        assert not result.failed(check), failing(result, check)
    assert result.checks_run["time check at :00 and :30 within 1 s"] >= 1


async def test_item_on_air_and_the_next_one_deleted(tmp_path):
    """V3-34 / V3-52m: the queue rows of the song on air and of the song the engine has
    already loaded are deleted. Nothing is silent, nothing airs twice, the 8 PM break is
    on time and the play log still says what aired."""
    async def remove(sim):
        eng = sim.engines[MAIN]
        ids = [uuid.UUID(i.entry_id) for i in (eng.on_air_item, eng.cued) if i is not None and i.entry_id]
        async with sim.Session() as db:
            await db.execute(delete(QueueEntry).where(QueueEntry.id.in_(ids)))
            await db.commit()

    result = await run(tmp_path, at(19, 44), 0.33, events=[(at(19, 50), remove)])
    for check in ON_TIME + ("no dead air or emergency music", LOG):
        assert not result.failed(check), failing(result, check)


async def test_a_15_minute_song_is_paused_for_the_time_check_and_carries_on(tmp_path):
    """V1-117 / V3-52i / F-ENGINE2-22: any item of 12 minutes or more counts as long. A
    15-minute song on air at 8 PM is paused for the time check, not cut, and carries on
    from where it stopped: three play-log rows in order, one resume."""
    async def lengthen(sim):
        async with sim.Session() as db:
            ids = (await db.execute(
                select(QueueEntry.asset_id).join(Asset, Asset.id == QueueEntry.asset_id).where(
                    QueueEntry.station_id == uuid.UUID(MAIN), QueueEntry.status == "pending",
                    QueueEntry.preempt_at.is_(None), Asset.asset_type == "music")
                .order_by(QueueEntry.position).offset(2).limit(60))).scalars().all()
            for asset in (await db.execute(select(Asset).where(Asset.id.in_(ids)))).scalars():
                asset.duration = 900.0
                meta = dict(asset.metadata_extra or {})
                meta.pop("audio_analysis", None)
                asset.metadata_extra = meta
            await db.commit()

    result = await run(tmp_path, at(19, 40), 0.75, events=[(at(19, 41), lengthen)],
                       blocks={"af3e8634-7c23-4c08-9742-ad93f516f147": {"is_active": False}})
    for check in ON_TIME + (LOG, RESUME):
        assert not result.failed(check), failing(result, check)
    songs = [r for r in result.rows if r.kind == "music" and r.cut and r.local_end.endswith("20:00:00.00")]
    assert songs, "no song was on air at 8 PM"
    assert result.checks_run[RESUME] >= 1, "the long song was cut, not paused"
    assert any(r.title == songs[0].title and r.cue_in > 0 for r in result.rows), "it never carried on"


async def test_a_block_whose_season_ends_today_stops_at_midnight(tmp_path):
    """V3-64: the hourly schedule announcement's season is made to end on 19 November. It
    airs at 11 PM and not at midnight, though tomorrow's queue was filled before midnight;
    the midnight time check still airs on its second."""
    block = "ad4a9918-a0ca-4732-b833-52651395e684"
    result = await run(tmp_path, at(22, 52), 1.25,
                       blocks={block: {"season_start": "01-01", "season_end": "11-19"}})
    mine = [r.local_start for r in result.rows if r.source == "block" and r.title.startswith("KBR schedule")]
    assert any(t.startswith("Thu 23:0") for t in mine), mine
    assert not [t for t in mine if t.startswith("Fri")], mine
    for check in ON_TIME:
        assert not result.failed(check), failing(result, check)
    assert result.checks_run["time check at :00 and :30 within 1 s"] >= 2
