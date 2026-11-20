"""Station simulator scenarios (tests/sim, wiki/station-simulator.md).

The real scheduler pass, playout-engine link, queue fill, program blocks and sponsorship
planner run on a virtual clock against the stations' real setup (a trimmed export of
production, tests/sim/fixtures), with a stand-in for the stream engine. A broadcast hour
takes about a minute, so only short windows run here; whole days and weeks run with

    cd backend && uv run python -m tests.sim.run --export tests/sim/fixtures/stations-2026-11-19.json \
        --start 2026-11-19T00:00 --hours 24 --stations main

or here with SIM_LONG=1. Every check is listed in tests/sim/checks.py.
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.sim.run import simulate

NY = ZoneInfo("America/New_York")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
LONG = os.environ.get("SIM_LONG") == "1"


async def _run(tmp_path, start, hours, stations):
    _, result = await simulate(EXPORT, start, hours, stations, tmp_path)
    print(result.report())
    return result


def _failing(result, check):
    return [f"{f.at}: {f.text}" for f in result.failed(check)]


@pytest.mark.asyncio
async def test_main_top_of_the_hour_into_the_daf_yomi(tmp_path):
    """Main, Wednesday 2026-11-18 7:50 to 8:20 PM ET: the 8 PM time check, weather and block
    on their second, then the Daf Yomi; no dead air, IDs and sponsors apart, no other
    station's items."""
    result = await _run(tmp_path, datetime(2026, 11, 18, 19, 50, tzinfo=NY), 0.5, ["main"])
    for check in ("hard items start on their second", "time check at :00 and :30 within 1 s",
                  "no dead air or emergency music", "time check says the right time",
                  "same sponsor never twice within 20 min", "no queue entry airs twice"):
        assert not result.failed(check), _failing(result, check)
    assert result.checks_run["time check at :00 and :30 within 1 s"] >= 1
    assert any(r.kind == "daf_yomi" for r in result.rows)


@pytest.mark.asyncio
async def test_no_sponsorship_inside_the_daf_yomi(tmp_path):
    """Main 7:40 to 8:46 PM: the Daf block is queued in the same pass as the sponsor breaks
    around it; none may land inside it (PR 406: the blocks' own shiurim count)."""
    _, result = await simulate(EXPORT, datetime(2026, 11, 18, 19, 40, tzinfo=NY), 1.1, ["main"], tmp_path,
                               quiet_pass=12.0)
    print(result.report())
    assert any(r.kind == "daf_yomi" for r in result.rows)
    assert result.checks_run["no sponsorship inside a shiur or story"] >= 1
    assert not result.failed("no sponsorship inside a shiur or story")
    # The 8:30 time check and weather pause the Daf, which then carries on where it
    # stopped with no quiet in between (review 2026-11-19: a pause, then a resume, is
    # where a gap of dead air would hide)
    resumed = [r for r in result.rows if r.kind == "daf_yomi" and (r.source or "").startswith("resume@")]
    assert resumed, "the Daf was not paused and resumed"
    for check in ("paused shiur: one resume per pause, no audio lost or repeated",
                  "no dead air or emergency music", "time check at :00 and :30 within 1 s"):
        assert result.checks_run[check] >= 1, check
        assert not result.failed(check), _failing(result, check)


@pytest.mark.asyncio
async def test_shabbos_starts_with_silence_at_candle_lighting(tmp_path):
    """Main, Friday 2026-11-20 around candle lighting (4:18 PM ET): the item on air fades,
    only the silence item airs; and the window itself is candle lighting to 72 min after
    shekia (Shmuel 2026-11-19)."""
    result = await _run(tmp_path, datetime(2026, 11, 20, 16, 5, tzinfo=NY), 0.4, ["main"])
    check = "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"
    assert result.checks_run[check] >= 1
    assert not result.failed(check), _failing(result, check)
    assert not result.failed("no dead air or emergency music"), _failing(result, "no dead air or emergency music")


@pytest.mark.skipif(not LONG, reason="a whole day per station: SIM_LONG=1")
@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["main", "jerusalem", "stories"])
async def test_a_whole_day(tmp_path, station):
    result = await _run(tmp_path, datetime(2026, 11, 19, 0, 0, tzinfo=NY), 24, [station])
    assert not result.failed(), result.report()


@pytest.mark.skipif(not LONG, reason="the relay runs on real threads, so timing varies between runs: SIM_LONG=1")
@pytest.mark.asyncio
async def test_fm_box_with_its_line_cut_for_half_an_hour(tmp_path):
    """The local FM box (real relay, infra/local/kbr_relay.py) loses its internet line
    across the 6 PM time check while staff change the queue on the site: the box keeps to
    the plan on its disk, the time check is on its second, nothing is silent, and when
    the line is back every play is in the site's log at the time it really aired."""
    from tests.sim.offline import run

    at = lambda h, m: datetime(2026, 11, 19, h, m, tzinfo=NY)  # noqa: E731
    result, notes, text = await run(EXPORT, at(17, 40), at(17, 50), at(18, 20), at(18, 30))
    print(text)
    for check in ("hard items start on their second", "time check at :00 and :30 within 1 s",
                  "no dead air or emergency music", "no queue entry airs twice"):
        assert not result.failed(check), _failing(result, check)
    joined = "\n".join(notes)
    assert "off their second: 0" in joined
    assert "0 at a wrong time, 0 not logged" in joined
    assert "(0 emergency-music stretches" in joined


# "Time to Say Good Shabbos" (Lev Tahor) as measured on the PC 2026-11-19: music from
# 0.23 s, silence from 276.52 s to the end of the 279.48 s file
GOOD_SHABBOS = {
    "id": "e1ed49c2-10ad-411e-b58e-95c51d048a8e", "title": "Time to Say Good Shabbos",
    "asset_type": "music", "category": "top_hits", "duration": 279.48,
    "analysis": {"version": 2, "cue_in_seconds": 0.23, "cue_out_seconds": 276.52},
}
SHOFAR = {"id": "5f0f0a00-0000-4000-8000-00000000500f", "title": "Shofar (sim stand-in)",
          "asset_type": "jingle", "category": "shabbos_ending", "duration": 21.0}


def _ending(after: bool = True) -> dict:
    return {"shabbos_ending": {"enabled": True, "song_asset_id": GOOD_SHABBOS["id"],
                               "after_asset_id": SHOFAR["id"] if after else None}}


@pytest.mark.asyncio
@pytest.mark.parametrize("station,start", [
    ("main", datetime(2026, 11, 20, 15, 55, tzinfo=NY)),
    # KBR Jerusalem, windows made again (candle lighting 3:57 PM Jerusalem = 8:57 AM ET)
    ("jerusalem", datetime(2026, 11, 20, 8, 30, tzinfo=NY)),
])
async def test_good_shabbos_song_ends_at_candle_lighting_then_shofar(tmp_path, station, start):
    """Erev Shabbos (Shmuel 2026-11-19): the song ends exactly at candle lighting, the
    shofar starts right then, then only silence; no time check, sponsorship or station ID
    in between; the station's window is still candle lighting to shekia + 72."""
    from tests.sim.run import PICK

    sid = PICK[station]
    _, result = await simulate(EXPORT, start, 0.6, [station], tmp_path, overrides={sid: _ending()},
                               extra_assets=[GOOD_SHABBOS, SHOFAR], regenerate_windows=True)
    print(result.report())
    for check in ("Erev Shabbos: song ends at candle lighting, shofar, then silence",
                  "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"):
        assert result.checks_run[check] >= 1
        assert not result.failed(check), _failing(result, check)
    # The real engine stays quiet (its hold lane, playout_engine.HOLD_BEFORE_HARD_SECONDS)
    # when the song on air ends under 8 s before a hard item; the stand-in engine has no
    # hold lane and logs those seconds as emergency music. Only that case is let through.
    dead = [f for f in result.failed("no dead air or emergency music")
            if not (f.text.startswith("emergency music for") and float(f.text.split()[3]) < 8.0)]
    assert not dead, [f"{f.at}: {f.text}" for f in dead]
    assert not result.failed("no queue entry airs twice"), _failing(result, "no queue entry airs twice")


@pytest.mark.asyncio
async def test_good_shabbos_song_without_a_shofar(tmp_path):
    """Until there is a shofar recording: the song ends at candle lighting and the silence
    starts right after it."""
    from tests.sim.run import PICK

    _, result = await simulate(EXPORT, datetime(2026, 11, 20, 15, 55, tzinfo=NY), 0.6, ["main"], tmp_path,
                               overrides={PICK["main"]: _ending(after=False)}, extra_assets=[GOOD_SHABBOS])
    print(result.report())
    for check in ("Erev Shabbos: song ends at candle lighting, shofar, then silence",
                  "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"):
        assert result.checks_run[check] >= 1
        assert not result.failed(check), _failing(result, check)


@pytest.mark.asyncio
async def test_sponsorships_wait_for_stories_on_all_day_stories(tmp_path):
    """All Day Stories, Thursday 2026-11-19 10 AM to 12:45 PM ET (coordinator 2026-11-19): with
    sponsorships_wait_for_talks a story is never cut or paused for a sponsorship; they play
    between stories. (Without the setting they cut in; tests/test_soft_items_pause_shiur.py
    covers both ways quickly.)"""
    from tests.sim.stations import STORIES

    overrides = {STORIES: {"sponsorships_wait_for_talks": True}}
    _, result = await simulate(EXPORT, datetime(2026, 11, 19, 10, 0, tzinfo=NY), 2.75, ["stories"], tmp_path,
                               overrides=overrides)
    print(result.report())
    check = "no sponsorship inside a shiur or story"
    assert result.checks_run[check] >= 1
    # only the sponsorship findings (a story cut by the hourly time check is the
    # engine's rule, not this one)
    sponsor_cuts = [f for f in _failing(result, check) if "sponsorship" in f]
    assert not sponsor_cuts, sponsor_cuts
    assert not result.failed("same sponsor never twice within 20 min"), _failing(result, "same sponsor never twice within 20 min")
    assert any(r.source == "ad_slot" for r in result.rows), "no sponsorship aired at all"


SPOKEN_CUT = "Shabbos/Yom Tov: no spoken item cut by candle lighting"
SHABBOS_IDS = {"main": "95358e09-1260-48d5-85b5-f851000773c0",
               "jerusalem": "d0740efe-3f25-4067-96f8-d8bc84c64dc3",
               "stories": "13c2697e-b9f8-42e8-b97a-4e1afa3b5a0e"}


def _short_shabbos(tmp_path, station: str, minutes: int):
    """The export with this station's Shabbos shortened to `minutes` (the edges are what
    is tested; a whole Shabbos takes an hour or more to run)."""
    import json
    from datetime import timedelta

    data = json.loads(EXPORT.read_text())
    for w in data["holiday_windows"]:
        if SHABBOS_IDS[station] in ((w.get("affected_stations") or {}).get("station_ids") or []):
            ws = datetime.fromisoformat(w["start_datetime"])
            w["end_datetime"] = (ws + timedelta(minutes=minutes)).isoformat()
            path = tmp_path / "export.json"
            path.write_text(json.dumps(data))
            return path, ws, ws + timedelta(minutes=minutes)
    raise AssertionError(f"no Shabbos window for {station}")


@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["main", "jerusalem", "stories"])
async def test_nothing_from_erev_shabbos_airs_on_motzei_shabbos(tmp_path, station):
    """Production queue read 2026-11-19 21:22Z: rows drawn for Friday afternoon (songs, a
    shiur, a sponsorship) still waited ahead of the Shabbos silences, which are hard timed
    and jump ahead of them at candle lighting; they would have aired first on Motzei
    Shabbos (Main: TEST SPONSOR 50 and Erev Shabbos songs; Jerusalem: Yoma 9, 51 min).

    Three minutes before candle lighting a song, a sponsorship and a shiur are put ahead of
    the silences, as the queue had them. Through Shabbos (shortened to 20 min, to keep CI
    under its time limit) into the half hour after it: none of what waited ahead of the silences airs after candle
    lighting, programming is back within seconds of the window's end with something new,
    and no spoken item is cut by candle lighting."""
    from datetime import timedelta

    from sqlalchemy import func, select, update

    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry

    export, ws, we = _short_shabbos(tmp_path, station, 20)
    sid = SHABBOS_IDS[station]
    before: set[str] = set()

    async def leftovers(sim):
        async with sim.Session() as db:
            silence = (await db.execute(select(Asset.id).where(Asset.asset_type == "silence").limit(1))).scalar()
            first = (await db.execute(select(func.min(QueueEntry.position)).where(
                QueueEntry.station_id == sid, QueueEntry.status == "pending",
                QueueEntry.asset_id == silence))).scalar()
            assert first is not None, "no silence queued three minutes before candle lighting"
            picks = []
            for kind, like in (("music", None), ("spot", "TEST SPONSOR%"), ("shiur", None)):
                q = select(Asset.id).where(Asset.asset_type == kind)
                if like:
                    q = q.where(Asset.title.like(like))
                aid = (await db.execute(q.order_by(Asset.title).limit(1))).scalar()
                if aid is not None:
                    picks.append(aid)
            await db.execute(update(QueueEntry).where(
                QueueEntry.station_id == sid, QueueEntry.status == "pending",
                QueueEntry.position >= first).values(position=QueueEntry.position + len(picks)))
            for i, aid in enumerate(picks):
                db.add(QueueEntry(station_id=sid, asset_id=aid, position=first + i, status="pending",
                                  source="auto"))
            await db.commit()
            rows = (await db.execute(select(QueueEntry.id).where(
                QueueEntry.station_id == sid, QueueEntry.status == "pending",
                QueueEntry.position < first + len(picks), QueueEntry.asset_id != silence,
                # (rows timed for after the window, e.g. its time checks, are not leftovers)
                (QueueEntry.preempt_at.is_(None)) | (QueueEntry.preempt_at < ws)))).scalars().all()
            before.update(str(r) for r in rows)

    start = ws - timedelta(minutes=25)
    hours = (we - start).total_seconds() / 3600 + 0.5
    _, result = await simulate(export, start, hours, [station], tmp_path,
                               events=[(ws - timedelta(minutes=3), leftovers)])
    print(result.report())
    assert before, "nothing was waiting ahead of the silences"
    rows = [r for r in result.rows if r.station == sid]
    late = [r.label() for r in rows if r.entry_id in before and r.start > ws.timestamp() + 1]
    assert not late, f"queued for before candle lighting, aired after it: {late}"
    back = next((r for r in rows if r.kind != "silence" and r.lane != "emergency"
                 and r.start > we.timestamp() - 1), None)
    assert back is not None, "nothing aired after Shabbos"
    assert back.start - we.timestamp() <= 5, f"back {back.start - we.timestamp():.0f} s late: {back.label()}"
    for check in ("Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia", SPOKEN_CUT):
        assert result.checks_run[check] >= 1, check
        assert not result.failed(check), _failing(result, check)




def _shabbos_window(station: str):
    import json

    data = json.loads(EXPORT.read_text())
    for w in data["holiday_windows"]:
        if SHABBOS_IDS[station] in ((w.get("affected_stations") or {}).get("station_ids") or []):
            return datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])
    raise AssertionError(f"no Shabbos window for {station}")


async def _full_shabbos(tmp_path, station, start, hours):
    """A real-length Shabbos (the export's window, 25 to 26 hours: 300+ silence rows of
    5 minutes, as production queues them). KBR Jerusalem 2026-11-20 13:57Z: 107 of its
    311 silences went to the engine's timed lane as one group, the engine never got them
    ready and its emergency playlist aired for 1 h 49 min. Earlier tests used windows of
    3 or 4 silences, where nothing of the kind can happen."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry

    ws, we = _shabbos_window(station)
    sid = SHABBOS_IDS[station]
    queued: list[int] = []

    async def count(sim):
        async with sim.Session() as db:
            silence = (await db.execute(select(Asset.id).where(Asset.asset_type == "silence").limit(1))).scalar()
            queued.append((await db.execute(select(func.count(QueueEntry.id)).where(
                QueueEntry.station_id == sid, QueueEntry.status == "pending",
                QueueEntry.asset_id == silence))).scalar())

    _, result = await simulate(EXPORT, start, hours, [station], tmp_path,
                               events=[(ws - timedelta(minutes=1), count)])
    print(result.report())
    expected = int((we - ws).total_seconds() // 300)
    assert queued and queued[0] >= expected, f"{queued} silence rows queued a minute before candle lighting, {expected} expected"
    for check in ("Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia",
                  "every timed group small enough for the engine", "no dead air or emergency music"):
        assert result.checks_run[check] >= 1, check
        assert not result.failed(check), _failing(result, check)
    return result, ws, we


@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["main", "jerusalem", "stories"])
async def test_a_full_length_shabbos_starts_with_silence_not_emergency_music(tmp_path, station):
    """The first 40 minutes of a real-length Shabbos (all 300+ silences queued): silence
    from candle lighting, no emergency music, no timed group too big for the engine."""
    from datetime import timedelta

    ws, _ = _shabbos_window(station)
    await _full_shabbos(tmp_path, station, ws - timedelta(minutes=10), 50 / 60)


@pytest.mark.skipif(not LONG, reason="a whole Shabbos per station: SIM_LONG=1")
@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["main", "jerusalem", "stories"])
async def test_a_full_length_shabbos_silent_to_the_end(tmp_path, station):
    """The whole real-length Shabbos and half an hour after: only silence from candle
    lighting to shekia + 72, then programming within 5 s."""
    from datetime import timedelta

    ws, we = _shabbos_window(station)
    start = ws - timedelta(minutes=10)
    result, ws, we = await _full_shabbos(tmp_path, station, start, (we - start).total_seconds() / 3600 + 0.5)
    rows = [r for r in result.rows if r.station == SHABBOS_IDS[station]]
    back = next((r for r in rows if r.kind != "silence" and r.lane != "emergency" and r.start > we.timestamp() - 1), None)
    assert back is not None and back.start - we.timestamp() <= 5, back.label() if back else "nothing after Shabbos"


SECOND_SILENCE = {"id": "5e1e0ce0-0000-4000-8000-000000000002", "title": "Silence (older copy)",
                  "asset_type": "silence", "duration": 300.0}


async def _shabbos_extended(tmp_path, caplog, station, hours_after_start, extend):
    """A Shabbos whose end is moved later while it is on (a window edited or remade mid-
    blackout), with a second silence item in the library and waiting rows of both, as
    production had. KBR Jerusalem
    2026-11-20 ~17:30Z: the refill during the window counted the rows of one silence item
    only, came up short on every pass, marked the silences played, queued them again and
    flushed and skipped the engine every few seconds (PR 480). The engine must be left
    alone: no flush or skip inside the window, each silence plays its whole length."""
    from datetime import timedelta

    from sqlalchemy import select, update

    import uuid
    from datetime import UTC

    from app.models.asset import Asset
    from app.models.holiday_window import HolidayWindow
    from app.models.queue_entry import QueueEntry

    ws, we = _shabbos_window(station)
    sid = SHABBOS_IDS[station]
    moved: list = []
    swapped: list = []

    async def extend_window(sim):
        async with sim.Session() as db:
            # Production queued rows of both silence items (which one a query took
            # without an order was up to Postgres): every other waiting silence row
            # becomes the second item
            waiting = (await db.execute(select(QueueEntry.id).join(Asset, Asset.id == QueueEntry.asset_id).where(
                QueueEntry.station_id == sid, QueueEntry.status == "pending",
                Asset.asset_type == "silence").order_by(QueueEntry.position))).scalars().all()
            await db.execute(update(QueueEntry).where(QueueEntry.id.in_(waiting[1::2]))
                             .values(asset_id=uuid.UUID(SECOND_SILENCE["id"])))
            swapped.append(len(waiting[1::2]))
            rows = (await db.execute(select(HolidayWindow))).scalars().all()
            for w in rows:
                if sid in ((w.affected_stations or {}).get("station_ids") or []):
                    end = w.end_datetime if w.end_datetime.tzinfo else w.end_datetime.replace(tzinfo=UTC)
                    await db.execute(update(HolidayWindow).where(HolidayWindow.id == w.id)
                                     .values(end_datetime=end + extend))
                    moved.append(w.id)
            await db.commit()

    start = ws - timedelta(minutes=10)
    caplog.set_level(logging.INFO, logger="app")
    sim, result = await simulate(EXPORT, start, hours_after_start, [station], tmp_path,
                                 extra_assets=[SECOND_SILENCE],
                                 events=[(ws + timedelta(minutes=20), extend_window)])
    print(result.report())
    assert moved, "no Shabbos window to extend"
    assert swapped and swapped[0] >= 100, f"waiting silence rows moved to the second item: {swapped}"
    # The refill clearing and requeueing the window's silences while one is on air (the
    # loop of 2026-11-20; in SQLite its clearing step fails outright instead of looping)
    refills = [r.getMessage()[:160] for r in caplog.records
               if ("Filled blackout queue" in r.getMessage() and ", active," in r.getMessage())
               or "fill_blackout_queue failed" in r.getMessage()]
    assert not refills, f"the window's silences were refilled while one was on air: {refills[:3]} ({len(refills)})"
    eng = sim.engines[sid]
    inside = [(round(t - ws.timestamp()), p) for t, p, _ in eng.commands
              if p in ("/flush", "/skip") and ws.timestamp() + 60 < t < (we + extend).timestamp() - 60]
    assert not inside, f"the engine was flushed or skipped inside the window: {inside[:10]} ({len(inside)})"
    rows = [r for r in result.rows if r.station == sid and r.kind == "silence" and r.lane != "emergency"
            and r.start > ws.timestamp() + 120 and r.end < sim.t - 1 and r.end < (we + extend).timestamp() - 60]
    short = [(r.local_start, round(r.seconds)) for r in rows if r.seconds < 290]
    assert not short, f"silences cut short inside the window: {short[:10]} ({len(short)} of {len(rows)})"
    for check in ("Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia",
                  "every timed group small enough for the engine", "no dead air or emergency music"):
        assert result.checks_run[check] >= 1, check
        assert not result.failed(check), _failing(result, check)
    return result, ws, we + extend


@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["main", "jerusalem"])
async def test_shabbos_extended_while_on_leaves_the_engine_alone(tmp_path, caplog, station):
    """The window's end moved an hour later 20 minutes into Shabbos: 50 minutes after
    candle lighting, no flush or skip, every silence whole."""
    from datetime import timedelta

    await _shabbos_extended(tmp_path, caplog, station, 1.0, timedelta(hours=1))


@pytest.mark.skipif(not LONG, reason="a whole Shabbos: SIM_LONG=1")
@pytest.mark.asyncio
async def test_shabbos_extended_while_on_silent_to_the_new_end(tmp_path, caplog):
    """Jerusalem's Shabbos moved an hour later while on: silence to the new end, then
    programming within 5 s."""
    from datetime import timedelta

    ws, we = _shabbos_window("jerusalem")
    hours = (we + timedelta(hours=1) - ws).total_seconds() / 3600 + 0.6
    result, ws, end = await _shabbos_extended(tmp_path, caplog, "jerusalem", hours, timedelta(hours=1))
    rows = [r for r in result.rows if r.station == SHABBOS_IDS["jerusalem"]]
    back = next((r for r in rows if r.kind != "silence" and r.lane != "emergency" and r.start > end.timestamp() - 1), None)
    assert back is not None and back.start - end.timestamp() <= 5, back.label() if back else "nothing after Shabbos"
