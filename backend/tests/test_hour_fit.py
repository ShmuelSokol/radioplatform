"""F-HOURFIT: the songs before a hard timed item are fitted to the time left.

Live on KBR Jerusalem (2026-11-17) a 5-minute song started at 20:58:59 and the
hard 21:00 time check cut it after 61 s. Shortly before a hard item the queue is
now reordered so the song on air at that moment loses as little as possible.
Listening-style checks: nothing is added or removed (no silence), the hard item
keeps its time, an empty queue is safe, and both players pick the new order.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import hour_fit
from app.services.hour_fit import fit_before_hard_item as _fit_station


async def fit_before_hard_item(db, station_id, now, *, force=True):
    station = await db.get(Station, station_id, populate_existing=True)
    return await _fit_station(db, station, now, force=force)

# An hour mark safely ahead of the real clock (the players' picks read it)
HOUR = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)


def _asset(title: str, seconds: float | None, *, kind: str = "music", artist: str | None = None) -> Asset:
    return Asset(
        id=uuid.uuid4(), title=title, artist=artist or f"{title} artist", duration=seconds,
        file_path=f"{uuid.uuid4().hex}.mp3", asset_type=kind, category="Music",
    )


async def _station(db: AsyncSession, config: dict | None = None) -> Station:
    station = Station(
        id=uuid.uuid4(), name=f"KBR Jerusalem {uuid.uuid4().hex[:6]}", timezone="Asia/Jerusalem", is_active=True,
        automation_config=config or {},
    )
    db.add(station)
    await db.flush()
    return station


async def _queue(db: AsyncSession, station: Station, on_air_ends: datetime, songs: list, *, timed: list = ()):
    """songs: (title, seconds) or (title, seconds, overrides). The item on air ends at
    `on_air_ends` (its crossfade included). A hard time check waits at HOUR."""
    station_id = station.id
    playing = _asset("On air", 200.0)
    db.add(playing)
    await db.flush()
    db.add(QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=playing.id, position=1, status="playing",
        source="auto", started_at=on_air_ends - timedelta(seconds=200.0 - hour_fit.CROSSFADE),
    ))
    position = 1
    for spec in songs:
        title, seconds, *rest = spec
        over = dict(rest[0]) if rest else {}
        asset = _asset(title, seconds, artist=over.pop("artist", None))
        db.add(asset)
        await db.flush()
        position += 1
        db.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id, position=position,
            status="pending", **{"source": "auto", **over},
        ))
    check = _asset("Time check 9 PM", 6.0, kind="jingle")
    db.add(check)
    await db.flush()
    position += 1
    db.add(QueueEntry(
        id=uuid.uuid4(), station_id=station_id, asset_id=check.id, position=position, status="pending",
        source="auto", preempt_at=HOUR, timing_mode="hard",
    ))
    for title, seconds, at, mode in timed:
        asset = _asset(title, seconds, kind="spot")
        db.add(asset)
        await db.flush()
        position += 1
        db.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id, position=position, status="pending",
            source="ad_slot", preempt_at=at, timing_mode=mode,
        ))
    await db.commit()


async def _order(db: AsyncSession, station_id) -> list[str]:
    db.expire_all()
    rows = await db.execute(
        select(Asset.title)
        .join(QueueEntry, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.preempt_at.is_(None))
        .order_by(QueueEntry.position)
    )
    return [r[0] for r in rows.all()]


async def _lost_at_the_hour(db: AsyncSession, station_id, on_air_ends: datetime) -> tuple[str, float, float]:
    """Play the songs forward: (title on air at the hour, seconds it played, seconds it loses)."""
    db.expire_all()
    rows = await db.execute(
        select(Asset.title, Asset.duration)
        .join(QueueEntry, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.preempt_at.is_(None))
        .order_by(QueueEntry.position)
    )
    at = on_air_ends
    for title, seconds in rows.all():
        end = at + timedelta(seconds=seconds - hour_fit.CROSSFADE)
        if end >= HOUR - timedelta(seconds=hour_fit.EARLY_START):
            played = (HOUR - at).total_seconds()
            return title, played, max(0.0, seconds - played)
        at = end
    raise AssertionError("the queue ran out of songs before the hour")


JERUSALEM = [("A", 240.0), ("B", 240.0), ("Five minutes", 300.0), ("Short", 180.0), ("Long", 310.0), ("Mid", 200.0)]
ENDS = HOUR - timedelta(minutes=9)  # the item on air ends 20:51:00
NOW = HOUR - timedelta(minutes=11)


@pytest.mark.asyncio
async def test_a_song_that_ends_at_the_hour_replaces_one_cut_after_a_minute(db_session: AsyncSession):
    """F-HOURFIT-1: the Jerusalem case. Unfitted, 'Five minutes' starts 66 s before the
    hour and loses 234 s. Fitted, the song on air at the hour loses a few seconds."""
    station = await _station(db_session)
    station_id = station.id
    await _queue(db_session, station, ENDS, JERUSALEM)
    title, played, lost = await _lost_at_the_hour(db_session, station_id, ENDS)
    assert (title, round(played), round(lost)) == ("Five minutes", 66, 234)
    before = sorted(await _order(db_session, station_id))
    hard_before = (await db_session.execute(
        select(QueueEntry.position, QueueEntry.preempt_at, QueueEntry.timing_mode)
        .where(QueueEntry.station_id == station_id, QueueEntry.preempt_at.isnot(None))
    )).all()

    assert await fit_before_hard_item(db_session, station_id, NOW, force=True) is True
    await db_session.commit()

    title, played, lost = await _lost_at_the_hour(db_session, station_id, ENDS)
    assert lost <= hour_fit.ACCEPTABLE_CUT, (title, played, lost)
    assert played > 200  # it was heard nearly whole, not faded after a minute
    # No silence, no late hard item: the same songs are all still queued, the time
    # check is untouched, and the song already next in line did not change
    order = await _order(db_session, station_id)
    assert sorted(order) == before and order[0] == "A"
    hard_after = (await db_session.execute(
        select(QueueEntry.position, QueueEntry.preempt_at, QueueEntry.timing_mode)
        .where(QueueEntry.station_id == station_id, QueueEntry.preempt_at.isnot(None))
    )).all()
    assert hard_after == hard_before
    # Once fitted, the next look changes nothing (no churn in Up next)
    assert await fit_before_hard_item(db_session, station_id, NOW + timedelta(seconds=30), force=True) is False
    assert await _order(db_session, station_id) == order


@pytest.mark.asyncio
async def test_both_players_pick_the_fitted_order(db_session: AsyncSession):
    """F-HOURFIT-2: the browser player's pick and the engine's pick both go by queue
    position, so each plays the fitted order."""
    from app.api.v1.queue import _pick_next_pending
    from app.services.playout_engine import pick_next

    station = await _station(db_session)
    station_id = station.id
    # The item on air ends 5 min before the hour; 'Five minutes' is second in line
    ends = HOUR - timedelta(minutes=5, seconds=10)
    await _queue(db_session, station, ends, [("Four", 242.0), ("Five minutes", 300.0), ("Fits", 305.0)])
    assert await fit_before_hard_item(db_session, station_id, ends - timedelta(minutes=2), force=True) is True
    await db_session.commit()
    order = await _order(db_session, station_id)
    assert order[0] == "Fits"  # one song runs to the hour instead of two with the second cut
    now = datetime.now(UTC)
    browser = await _pick_next_pending(db_session, station_id, now)
    engine = await pick_next(db_session, station_id)
    assert browser.asset.title == engine.asset.title == "Fits"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["operator", "held", "other_block", "hs_off", "switched_off", "starting_now"])
async def test_what_is_never_moved(db_session: AsyncSession, monkeypatch, case):
    """F-HOURFIT-3: only the station's own automatic songs move, within one block,
    never one the engine holds or that starts in a moment; HS off and the
    station's own switch leave the queue alone."""
    config = {"hard_start": False} if case == "hs_off" else {hour_fit.CONFIG_KEY: False} if case == "switched_off" else {}
    station = await _station(db_session, config)
    station_id = station.id
    songs = [list(s) for s in JERUSALEM]
    if case == "operator":
        songs[2] = ["Five minutes", 300.0, {"source": "manual"}]  # a person queued it: their order stands
        songs[1] = ["B", 240.0, {"source": "manual"}]
    if case == "other_block":
        for s in songs[:2] + songs[3:]:  # every other song belongs to another block
            s.append({"block_id": str(uuid.uuid4())})
    ends = ENDS
    now = NOW
    if case == "starting_now":
        songs = [["Five minutes", 300.0], ["Fits", 60.0]]
        ends = HOUR - timedelta(seconds=66)
        now = ends - timedelta(seconds=10)  # pages already know what is next
    await _queue(db_session, station, ends, [tuple(s) for s in songs])
    if case == "held":
        ids = set((await db_session.execute(
            select(QueueEntry.id).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        )).scalars().all())
        monkeypatch.setattr("app.services.playout_engine.held_entry_ids", lambda _sid: ids)
    order = await _order(db_session, station_id)
    assert await fit_before_hard_item(db_session, station_id, now, force=True) is False
    assert await _order(db_session, station_id) == order


@pytest.mark.asyncio
async def test_empty_queue_and_no_hard_item_are_safe(db_session: AsyncSession):
    """F-HOURFIT-4: nothing queued, only the time check queued, or no hard item in
    the next 20 minutes: nothing happens and nothing fails."""
    empty = await _station(db_session)
    empty_id = empty.id
    await db_session.commit()
    assert await fit_before_hard_item(db_session, empty_id, NOW, force=True) is False

    only_timed = await _station(db_session)
    only_timed_id = only_timed.id
    await _queue(db_session, only_timed, ENDS, [])
    assert await fit_before_hard_item(db_session, only_timed_id, NOW, force=True) is False

    far = await _station(db_session)
    far_id = far.id
    await _queue(db_session, far, ENDS, JERUSALEM)
    order = await _order(db_session, far_id)
    assert await fit_before_hard_item(db_session, far_id, HOUR - timedelta(minutes=45), force=True) is False
    assert await _order(db_session, far_id) == order


@pytest.mark.asyncio
async def test_a_soft_item_at_quarter_to_is_counted_and_not_crossed(db_session: AsyncSession):
    """F-HOURFIT-5: a soft sponsorship at :45 plays after its song and takes its own
    time; the songs after it are the ones fitted, and it keeps its place and time."""
    station = await _station(db_session)
    station_id = station.id
    quarter_to = HOUR - timedelta(minutes=15)
    ends = HOUR - timedelta(minutes=14, seconds=30)  # the song on air ends 20:45:30
    # Sponsorship 30 s (20:45:30-20:46:00), then A to 20:49:57, B to 20:53:54,
    # C to 20:57:51, then 'Five minutes' would start 129 s before the hour
    songs = [("A", 240.0), ("B", 240.0), ("C", 240.0), ("Five minutes", 300.0), ("Fits", 365.0), ("Mid", 200.0)]
    await _queue(db_session, station, ends, songs, timed=[("Sponsor", 30.0, quarter_to, "soft")])
    assert await fit_before_hard_item(db_session, station_id, ends - timedelta(minutes=1), force=True) is True
    await db_session.commit()
    order = await _order(db_session, station_id)
    assert order[:3] == ["A", "B", "Fits"], order  # 20:53:54 + 365 s = 20:59:59
    soft = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot")
    )).scalar_one()
    assert soft.status == "pending" and soft.timing_mode == "soft"


@pytest.mark.asyncio
async def test_same_artist_is_not_put_back_to_back(db_session: AsyncSession):
    """F-HOURFIT-6: a pair by one artist is not chosen when another fit exists."""
    station = await _station(db_session)
    station_id = station.id
    songs = [
        ("A", 240.0), ("B", 240.0), ("Five minutes", 300.0),
        ("Twin 1", 150.0, {"artist": "Same"}), ("Twin 2", 156.0, {"artist": "Same"}),
        ("Other", 157.0),
    ]
    await _queue(db_session, station, ENDS, songs)
    assert await fit_before_hard_item(db_session, station_id, NOW, force=True) is True
    await db_session.commit()
    order = await _order(db_session, station_id)
    pair = set(order[1:3])
    assert pair != {"Twin 1", "Twin 2"}, order
    _, _, lost = await _lost_at_the_hour(db_session, station_id, ENDS)
    assert lost <= hour_fit.ACCEPTABLE_CUT


@pytest.mark.asyncio
@pytest.mark.parametrize("player", ["browser", "engine"])
async def test_the_scheduler_pass_fits_the_queue_on_both_players(db_session: AsyncSession, monkeypatch, player):
    """F-HOURFIT-7: each scheduler pass does the fitting before it advances the
    browser player's queue or ticks the playout engine, and saves it."""
    from app.config import settings
    from app.services import playout_engine
    from app.services.scheduler_engine import SchedulerEngine

    station = await _station(db_session)
    station_id = station.id
    await _queue(db_session, station, ENDS, JERUSALEM)
    seen: list[tuple[str, list[str]]] = []

    async def fake_tick(db, st):
        seen.append(("engine", await _order(db, st.id)))

    async def fake_advance(self, db, sid):
        seen.append(("browser", await _order(db, sid)))

    async def fake_check(self, db, st):
        return None

    monkeypatch.setattr(playout_engine, "tick", fake_tick)
    monkeypatch.setattr(SchedulerEngine, "_advance_queue", fake_advance)
    monkeypatch.setattr(SchedulerEngine, "_check_station", fake_check)
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: player == "engine")
    monkeypatch.setattr(hour_fit, "HORIZON", timedelta(hours=3))  # HOUR is up to two hours off
    hour_fit._last_look.clear()

    await SchedulerEngine()._check_all_stations(db_session)
    assert [who for who, _ in seen] == [player]
    assert seen[0][1][:2] == ["A", "Long"], seen  # fitted before the player looked
    _, played, lost = await _lost_at_the_hour(db_session, station_id, ENDS)
    assert lost <= hour_fit.ACCEPTABLE_CUT and played > 200
    # The next pass (a second later) does not look again
    await SchedulerEngine()._check_all_stations(db_session)
    assert seen[1][1] == seen[0][1]


@pytest.mark.asyncio
async def test_a_fit_that_ends_just_after_the_hour_beats_one_that_ends_just_before(db_session: AsyncSession):
    """F-HOURFIT-8: song changes drift by a few seconds. A fit ending a few seconds
    early is one drift away from letting another song start (live, Jerusalem
    21:47: fitted to lose 0 s, then 219 s ten seconds later), so a song that
    runs a little past the hour is chosen over one that stops a little short."""
    station = await _station(db_session)
    station_id = station.id
    # Room for the last song: 300 s. 'Short of it' would end 5 s early, 'Past it' 6 s late.
    ends = HOUR - timedelta(seconds=300)
    await _queue(db_session, station, ends, [("Far too long", 500.0), ("Short of it", 298.0), ("Past it", 309.0)])
    assert await fit_before_hard_item(db_session, station_id, ends - timedelta(minutes=2)) is True
    await db_session.commit()
    assert (await _order(db_session, station_id))[0] == "Past it"


@pytest.mark.asyncio
async def test_a_songs_own_crossfade_point_is_used(db_session: AsyncSession):
    """F-HOURFIT-9: songs on this station hand over 3 to 12 s before their file ends.
    The fit counts each song up to its measured crossfade point, as the players do."""
    from app.models.asset import ANALYSIS_VERSION

    station = await _station(db_session)
    station_id = station.id
    ends = HOUR - timedelta(seconds=300)
    await _queue(db_session, station, ends, [("Far too long", 500.0), ("Long tail", 340.0), ("Plain", 325.0)])
    tail = (await db_session.execute(select(Asset).where(Asset.title == "Long tail"))).scalar_one()
    # Hands over at 302 s: 2 s past the hour, though the file runs 40 s past it
    tail.metadata_extra = {"audio_analysis": {"version": ANALYSIS_VERSION, "cross_start_seconds": 302.0}}
    await db_session.commit()
    assert await fit_before_hard_item(db_session, station_id, ends - timedelta(minutes=2)) is True
    await db_session.commit()
    assert (await _order(db_session, station_id))[0] == "Long tail"


@pytest.mark.asyncio
async def test_a_fit_that_ends_a_second_past_the_hour_is_not_trusted(db_session: AsyncSession):
    """F-HOURFIT-10: live, Jerusalem 23:00Z: the song fitted to end 1 s past the hour
    ended 1.9 s before it, so a 4-minute song was started and cut after 2.4 s. A song
    with a few seconds to spare past the hour is chosen over one that only just
    reaches it."""
    station = await _station(db_session)
    station_id = station.id
    ends = HOUR - timedelta(seconds=300)
    # Both counted to their hand-over point (file length less the 3 s overlap)
    await _queue(db_session, station, ends, [("Far too long", 500.0), ("Only just", 304.0), ("To spare", 312.0)])
    assert await fit_before_hard_item(db_session, station_id, ends - timedelta(minutes=2)) is True
    await db_session.commit()
    assert (await _order(db_session, station_id))[0] == "To spare"
