"""Checklist rows on sponsorship edge cases and the repeat rules.

I-7, V3-46, V3-47. Times are pinned (fixed days and the clock_off_the_slots fixture).
"""
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest

from app.models.asset import Asset
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.services.playlist_generator_service import generate
from app.services.spot_schedule_service import plan_spots, reconcile_spot_slots
from tests.test_block_songs_reach_queue import MONDAY_7AM, _block, _song
from tests.test_block_songs_reach_queue import _station as _block_station
from tests.test_spot_slot_churn import _refill, _rows, _station_with_contract
from tests.test_sponsorship_room import DAY, NY, _break, _spot

# ── I-7: a placeholder recording is never planned and churned ────────────────


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock_off_the_slots")
@pytest.mark.parametrize("fields", [
    {"category": "do_not_play"},
    {"title": True},
], ids=["do-not-play", "placeholder"])
async def test_a_sponsorship_that_may_not_air_makes_no_dead_rows(db_session, fields):
    """I-7: a sponsorship whose recording is do-not-play or a placeholder is not planned,
    so queue reads neither add nor remove its slots, pass after pass."""
    from app.models.asset import NEVER_AUTO_TITLES

    title = fields.pop("title", None)
    station, spot = await _station_with_contract(db_session, **fields)
    if title:  # the placeholder sponsorship recording, by its reserved title
        spot.title = NEVER_AUTO_TITLES[0]
        await db_session.commit()
    for _ in range(5):
        result = await reconcile_spot_slots(db_session, station.id, datetime.now(UTC))
        await db_session.commit()
        assert result is not None and result["added"] == 0 and result.get("removed", 0) == 0, result
        await _refill(db_session, station)
        assert await _rows(db_session, station) == []


# ── V3-46: the same sponsor twice in a row, or two in one block ──────────────


def _order(placed):
    return [(p.local.strftime("%H:%M"), p.schedule.name) for p in sorted(placed, key=lambda p: (p.at, p.seat))]


def test_two_sponsors_share_one_block_in_a_set_order():
    """V3-46: two different sponsorships may share one block's places; the order is
    fixed (the same plan every run) and both air."""
    a, b = _spot("alpha"), _spot("bravo")
    placed, unplaced = plan_spots([a, b], NY, DAY, 1, breaks=[_break(DAY, 19, places=4)])
    assert not unplaced
    assert sorted(n for _, n in _order(placed)) == ["alpha", "bravo"]
    again, _ = plan_spots([a, b], NY, DAY, 1, breaks=[_break(DAY, 19, places=4)])
    assert _order(again) == _order(placed)


def test_same_sponsor_twice_goes_to_different_blocks_when_there_are_some():
    """V3-46: one sponsor's two plays go to two different blocks, not back to back."""
    busy = _spot("busy", plays=2)
    placed, unplaced = plan_spots([busy, _spot("other")], NY, DAY, 1,
                                  breaks=[_break(DAY, 19, places=4), _break(DAY, 20, places=4)])
    assert not unplaced
    times = sorted(p.local for p in placed if p.schedule.name == "busy")
    assert len(times) == 2 and times[0] != times[1]


def test_same_sponsor_is_not_back_to_back_inside_one_block():
    """V3-46: with only one block that evening, one sponsor's second play has no place
    20 minutes from its first, so it is left out (and warned about) rather than aired
    twice in the same break (Shmuel 2026-11-18: never one sponsor twice within 20 min)."""
    busy = _spot("busy", plays=2)
    placed, unplaced = plan_spots([busy, _spot("other")], NY, DAY, 1, breaks=[_break(DAY, 19, places=10)])
    names = sorted(n for _, n in _order(placed))
    assert names == ["busy", "other"], names
    assert [u.schedule.name for u in unplaced] == ["busy"]


# ── V3-47: every song blocked by the repeat rules ────────────────────────────


@pytest.mark.asyncio
async def test_small_category_all_blocked_by_repeat_rules_still_plays_music(db_session):
    """V3-47: a block whose template draws from a two-song category, both songs by the
    artist just played and both just aired: the hour is still filled with music."""
    station, schedule, _filler = await _block_station(db_session, filler=0)
    tpl = PlaylistTemplate(id=uuid.uuid4(), name="Small", station_id=station.id, is_active=True)
    db_session.add(tpl)
    await db_session.flush()
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=tpl.id, position=0, asset_type="music",
                                category="Tiny"))
    tiny = [_song(f"Tiny {n}", category="Tiny", artist="One Artist") for n in range(2)]
    db_session.add_all(tiny)
    await db_session.flush()
    await _block(db_session, schedule, [], template_id=tpl.id, start=time(0, 0), end=time(23, 59))
    await db_session.commit()
    plan = await generate(db_session, station, MONDAY_7AM, 1,
                          recent_artists=["One Artist"] * 20, busy_ids={a.id for a in tiny})
    titles = [p.title for p in plan.picks]
    assert titles, "nothing was drawn: the hour would be silent"
    assert set(titles) <= {"Tiny 0", "Tiny 1"}
    covered = sum(p.duration or 0 for p in plan.picks)
    assert covered >= 3600 - 240, covered
    assert plan.picks[0].at <= MONDAY_7AM + timedelta(minutes=1)


def test_asset_used_here():
    assert Asset.__tablename__ == "assets"


# ── V3-80: the portal and the client page show what aired ───────────────────


@pytest.mark.asyncio
async def test_portal_and_client_page_list_exactly_what_aired(client, auth_headers, db_session):
    """V3-80: play by play, the sponsor's portal and the staff client page list the same
    plays as the play log: every aired play of the sponsor's recordings on every station,
    nothing of another sponsor, and a queued slot that never aired is not a play."""
    from app.core.security import hash_password
    from app.models.queue_entry import QueueEntry
    from app.models.user import User, UserRole
    from tests.test_sponsor_accounts import _asset as _spot_asset
    from tests.test_sponsor_accounts import _contract, _play, _setup, _sponsor

    main, jlm = await _setup(db_session)
    user = User(id=uuid.uuid4(), email="pita@test.com", hashed_password=hash_password("pw123456"),
                role=UserRole.SPONSOR, is_active=True)
    db_session.add(user)
    sp = _sponsor(db_session, "Pita Hut", user_id=user.id)
    other = _sponsor(db_session, "Someone Else")
    await db_session.flush()
    a = _spot_asset(db_session, "Pita Hut spot", sp)
    b = _spot_asset(db_session, "Pita Hut second spot", sp)
    x = _spot_asset(db_session, "Other spot", other)
    await db_session.flush()
    base = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    _contract(db_session, sp, a, main, base.date() - timedelta(days=5), base.date() + timedelta(days=5))
    aired = [(main, a, base), (main, a, base + timedelta(hours=2)), (jlm, b, base + timedelta(days=1)),
             (main, b, base + timedelta(hours=5, minutes=15))]
    for st, asset, at in aired:
        _play(db_session, st, asset, at)
    _play(db_session, main, x, base + timedelta(hours=1))  # another sponsor
    # a slot the queue holds as played that never reached the play log
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=main.id, asset_id=a.id, position=1, status="played",
                              source="ad_slot", preempt_at=base + timedelta(hours=3)))
    await db_session.commit()
    want = sorted((str(st.id), str(asset.id), at.replace(tzinfo=None)) for st, asset, at in aired)

    def naive(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)

    staff = (await client.get(f"/api/v1/sponsors/{sp.id}/plays?limit=100", headers=auth_headers)).json()
    got_staff = sorted((e["station_id"], e["asset_id"], naive(e["start_utc"])) for e in staff["entries"])
    assert staff["total"] == len(aired) and got_staff == want

    login = await client.post("/api/v1/auth/login", json={"email": "pita@test.com", "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    portal = (await client.get("/api/v1/sponsor-portal/play-history?limit=100", headers=headers)).json()
    names = {str(main.id): main.name, str(jlm.id): jlm.name}
    titles = {str(a.id): a.title, str(b.id): b.title}
    got_portal = sorted((e["station_name"], e["asset_title"], naive(e["start_utc"])) for e in portal["entries"])
    assert portal["total"] == len(aired)
    assert got_portal == sorted((names[s], titles[t], at) for s, t, at in want)


# ── V3-44: audio replaced after queueing; paused while a spot is on air ──────


async def _contract_with_queue(client, auth_headers, db_session):
    from tests.test_sponsorship_contracts import ALL, JLM, _station_with_contract_audio

    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    today = datetime.now(UTC).astimezone(JLM).date()
    r = await client.post("/api/v1/spot-schedules", json={
        "sponsor_id": sponsor, "asset_id": str(assets[0].id), "station_id": str(station.id),
        "dayparts": ALL, "plays_per_daypart": 1,
        "start_date": today.isoformat(), "end_date": (today + timedelta(days=3)).isoformat(),
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    return station, assets, r.json()["id"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock_off_the_slots")
async def test_audio_replaced_after_queueing_airs_the_new_recording(client, auth_headers, db_session):
    """V3-44: the recording of a contract is replaced after its plays were queued: every
    waiting slot now plays the new recording, none the old."""
    from tests.test_sponsorship_contracts import _pending

    station, assets, sched = await _contract_with_queue(client, auth_headers, db_session)
    station_id, old_id, new_id = station.id, str(assets[0].id), str(assets[1].id)
    before = await _pending(db_session, station_id)
    assert before and {str(e.asset_id) for e in before} == {old_id}
    count = len(before)
    r = await client.put(f"/api/v1/spot-schedules/{sched}", json={"asset_id": new_id}, headers=auth_headers)
    assert r.status_code == 200, r.text
    after = await _pending(db_session, station_id)
    assert len(after) == count
    assert {str(e.asset_id) for e in after} == {new_id}


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock_off_the_slots")
async def test_paused_or_deleted_contract_never_cuts_the_spot_on_air(client, auth_headers, db_session):
    """V3-44: a sponsorship paused, then deleted, while one of its plays is on air: the
    play on air is left to finish; the waiting ones go."""
    from app.models.queue_entry import QueueEntry
    from tests.test_sponsorship_contracts import _pending

    station, assets, sched = await _contract_with_queue(client, auth_headers, db_session)
    station_id = station.id
    waiting = await _pending(db_session, station_id)
    assert waiting
    on_air = waiting[0]
    on_air.status = "playing"
    on_air.started_at = datetime.now(UTC)
    on_air_id = on_air.id
    await db_session.commit()

    r = await client.put(f"/api/v1/spot-schedules/{sched}", json={"is_active": False}, headers=auth_headers)
    assert r.status_code == 200
    assert await _pending(db_session, station_id) == []
    db_session.expire_all()
    assert (await db_session.get(QueueEntry, on_air_id)).status == "playing"

    r = await client.delete(f"/api/v1/spot-schedules/{sched}", headers=auth_headers)
    assert r.status_code in (200, 204)
    db_session.expire_all()
    still = await db_session.get(QueueEntry, on_air_id)
    assert still is not None and still.status == "playing"
