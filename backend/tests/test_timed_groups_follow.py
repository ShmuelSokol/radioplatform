"""V1-10b: a hard timed group due while an earlier short hard group is still playing
follows it instead of cutting it, on the browser player and on the engine."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import playout_engine
from app.services.timed_groups import space_hard_groups
from tests import test_engine_backend as _engine_tests
from tests.test_engine_backend import _add, _fresh

eng = _engine_tests.eng  # the engine fixture (faked engine commands)


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def nothing(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)


def _asset(db, title, kind="jingle", duration=6.0):
    a = Asset(id=uuid.uuid4(), title=title, duration=float(duration), asset_type=kind,
              file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(a)
    return a


async def _station(db):
    st = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db.add(st)
    await db.flush()
    return st


def _entry(st, asset, at, pos, **kw):
    return QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=asset.id, position=pos,
                      status=kw.pop("status", "pending"), source=kw.pop("source", "auto"),
                      preempt_at=at, timing_mode=kw.pop("timing_mode", "hard"), **kw)


async def _at(db, entry_id):
    row = (await db.execute(select(QueueEntry).where(QueueEntry.id == entry_id)
                            .execution_options(populate_existing=True))).scalar_one()
    at = row.preempt_at
    return at if at.tzinfo else at.replace(tzinfo=UTC)


# ── The rule ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_group_follows_a_time_check_one_second_earlier(db_session):
    """Time check (6 s) at T, announcement at T+1 s: the announcement moves to T+6 s."""
    st = await _station(db_session)
    clock, ann = _asset(db_session, "Time"), _asset(db_session, "Announce", duration=8)
    await db_session.flush()
    t = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    a = _entry(st, clock, t, 1)
    b = _entry(st, ann, t + timedelta(seconds=1), 2)
    db_session.add_all([a, b])
    await db_session.commit()
    now = datetime.now(UTC)
    moved = await space_hard_groups(db_session, st.id, now, now + timedelta(seconds=60))
    await db_session.commit()
    assert moved == {str(b.id): t}
    assert await _at(db_session, b.id) == t + timedelta(seconds=6)
    assert await _at(db_session, a.id) == t


@pytest.mark.asyncio
async def test_chain_of_three_groups_keeps_the_first_as_leader(db_session):
    st = await _station(db_session)
    x, y, z = (_asset(db_session, n) for n in ("Time", "ID", "Promo"))
    await db_session.flush()
    t = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    ea, eb, ec = (_entry(st, x, t, 1), _entry(st, y, t + timedelta(seconds=1), 2),
                  _entry(st, z, t + timedelta(seconds=2), 3))
    db_session.add_all([ea, eb, ec])
    await db_session.commit()
    now = datetime.now(UTC)
    moved = await space_hard_groups(db_session, st.id, now, now + timedelta(seconds=60))
    await db_session.commit()
    assert moved == {str(eb.id): t, str(ec.id): t}
    assert await _at(db_session, eb.id) == t + timedelta(seconds=6)
    assert await _at(db_session, ec.id) == t + timedelta(seconds=12)


@pytest.mark.asyncio
async def test_group_far_enough_apart_is_left_alone(db_session):
    st = await _station(db_session)
    x, y = _asset(db_session, "Time"), _asset(db_session, "Weather")
    await db_session.flush()
    t = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    db_session.add_all([_entry(st, x, t, 1), _entry(st, y, t + timedelta(seconds=6), 2)])
    await db_session.commit()
    now = datetime.now(UTC)
    assert await space_hard_groups(db_session, st.id, now, now + timedelta(seconds=60)) == {}


@pytest.mark.asyncio
async def test_nothing_waits_behind_a_shiur_or_for_silence(db_session):
    """A shiur in a group is the long-item rule's business; silence (Shabbos) is never
    moved and never waited for; a move of more than two minutes is not made."""
    st = await _station(db_session)
    shiur = _asset(db_session, "Daf", kind="shiur", duration=40 * 60)
    clock = _asset(db_session, "Time")
    silence = _asset(db_session, "Silence", kind="silence", duration=3600)
    long_spot = _asset(db_session, "Long", duration=600)
    after_long = _asset(db_session, "After long")
    await db_session.flush()
    t = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    db_session.add_all([
        _entry(st, shiur, t, 1), _entry(st, clock, t + timedelta(seconds=1), 2),
        _entry(st, silence, t + timedelta(seconds=2), 3),
        _entry(st, long_spot, t + timedelta(seconds=20), 4),
        _entry(st, after_long, t + timedelta(seconds=21), 5),
    ])
    await db_session.commit()
    now = datetime.now(UTC)
    assert await space_hard_groups(db_session, st.id, now, now + timedelta(seconds=60)) == {}


@pytest.mark.asyncio
async def test_frozen_entries_never_move(db_session):
    st = await _station(db_session)
    x, y = _asset(db_session, "Time"), _asset(db_session, "Weather")
    await db_session.flush()
    t = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    b = _entry(st, y, t + timedelta(seconds=1), 2)
    db_session.add_all([_entry(st, x, t, 1), b])
    await db_session.commit()
    now = datetime.now(UTC)
    assert await space_hard_groups(db_session, st.id, now, now + timedelta(seconds=60),
                                   frozen={str(b.id)}) == {}


# ── Browser player ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_time_check_on_air_is_not_cut_by_the_next_group(db_session):
    """The time check started 2 s ago (6 s long); a group due 1 s ago must wait for it,
    not cut it."""
    st = await _station(db_session)
    clock, ann, song = _asset(db_session, "Time"), _asset(db_session, "Announce"), \
        _asset(db_session, "Song", kind="music", duration=200)
    await db_session.flush()
    now = datetime.now(UTC)
    t = now - timedelta(seconds=2)
    db_session.add(_entry(st, clock, t, 1, status="playing", started_at=t))
    b = _entry(st, ann, t + timedelta(seconds=1), 2)
    db_session.add_all([b, QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id,
                                      position=3, status="pending", source="auto")])
    await db_session.commit()
    on_air = await queue_module._check_advance(db_session, st.id)
    assert on_air.asset_id == clock.id
    assert await _at(db_session, b.id) == t + timedelta(seconds=6)
    assert (await _fresh(db_session, b.id)).status == "pending"


# ── Engine ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_gets_the_second_group_as_part_of_the_first(db_session, eng):
    """The announcement goes to the engine with the time check's time, so the engine plays
    it after the time check (same take-over) instead of taking over again 1 s in."""
    station, _, engine, _ = eng
    t = (datetime.now(UTC) + timedelta(seconds=30)).replace(microsecond=0)
    a = await _add(db_session, station, title="Time", duration=6.0, kind="time_announcement",
                   preempt_at=t, timing_mode="hard", position=0)
    b = await _add(db_session, station, title="Announce", duration=8.0, kind="jingle",
                   preempt_at=t + timedelta(seconds=1), timing_mode="hard", position=0)
    st = await db_session.get(Station, station.id)
    await playout_engine.tick(db_session, st)
    sent = {c["id"]: c["at"] for c in engine.timed()}
    assert sent[str(a.id)] == pytest.approx(t.timestamp())
    assert sent[str(b.id)] == pytest.approx(t.timestamp())
    assert await _at(db_session, b.id) == t + timedelta(seconds=6)
