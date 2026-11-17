"""Song request matching (song_request_service.fuzzy_match_asset).

2026-11-16: a listener on KBR Jerusalem typed "One day by matisyahu" in the title box and
left the artist empty; the library has "One Day" by Matisyahu, but the request scored
0.35 and was not matched.
"""
import uuid

import pytest

from app.models.asset import Asset
from app.services.song_request_service import fuzzy_match_asset


async def _library(db):
    songs = [("One Day", "Matisyahu"), ("One Day", "Shlomo Simcha"), ("Rachem", "Yaakov Shwekey"),
             ("King Without a Crown", "Matisyahu"), ("Day by Day", "Avraham Fried")]
    made = {}
    for title, artist in songs:
        a = Asset(id=uuid.uuid4(), title=title, artist=artist, duration=200.0,
                  file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3", asset_type="music")
        db.add(a)
        made[(title, artist)] = a
    await db.commit()
    return made


@pytest.mark.asyncio
@pytest.mark.parametrize("title,artist,expected", [
    ("One day by matisyahu", None, ("One Day", "Matisyahu")),
    pytest.param("One Day - Matisyahu", None, ("One Day", "Matisyahu"), marks=pytest.mark.xfail(strict=True, reason="matcher splits only on ' by ' (PR 144); dash and artist-first not handled")),
    pytest.param("Matisyahu - One Day", None, ("One Day", "Matisyahu"), marks=pytest.mark.xfail(strict=True, reason="matcher splits only on ' by ' (PR 144); dash and artist-first not handled")),
    pytest.param("matisyahu one day", None, ("One Day", "Matisyahu"), marks=pytest.mark.xfail(strict=True, reason="matcher splits only on ' by ' (PR 144); dash and artist-first not handled")),
    ("One Day", "Matisyahu", ("One Day", "Matisyahu")),
    ("Rachem", "Shwekey", ("Rachem", "Yaakov Shwekey")),
    ("Day by Day", None, ("Day by Day", "Avraham Fried")),
])
async def test_request_finds_the_song(db_session, title, artist, expected):
    made = await _library(db_session)
    asset, score = await fuzzy_match_asset(db_session, title, artist, str(uuid.uuid4()))
    assert asset is not None, f"{title!r} matched nothing"
    assert asset.id == made[expected].id, f"{title!r} matched {asset.title} / {asset.artist} ({score:.2f})"
    assert score >= 0.6


@pytest.mark.asyncio
async def test_unknown_song_matches_nothing(db_session):
    await _library(db_session)
    asset, score = await fuzzy_match_asset(db_session, "Completely different request", None, str(uuid.uuid4()))
    assert asset is None and score == 0.0


@pytest.mark.asyncio
async def test_matching_an_approved_request_by_hand_queues_it(client, db_session, auth_headers):
    """The "One Day" request was approved with no match: matching it by hand queues the song."""
    from sqlalchemy import select

    from app.models.queue_entry import QueueEntry
    from app.models.song_request import RequestStatus, SongRequest
    from app.models.station import Station

    made = await _library(db_session)
    st = Station(id=uuid.uuid4(), name="Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(st)
    req = SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="Listener",
                      song_title="One day by matisyahu", status=RequestStatus.APPROVED)
    db_session.add(req)
    await db_session.commit()
    song = made[("One Day", "Matisyahu")]

    r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(song.id)}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    queued = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == st.id))).scalars().all()
    assert [str(q.asset_id) for q in queued] == [str(song.id)]

    bad = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(uuid.uuid4())}, headers=auth_headers)
    assert bad.status_code == 404


async def _approved_request(db, station):
    from app.models.song_request import RequestStatus, SongRequest

    req = SongRequest(id=uuid.uuid4(), station_id=station.id, requester_name="Listener",
                      song_title="One day by matisyahu", status=RequestStatus.APPROVED)
    db.add(req)
    await db.commit()
    return req


@pytest.mark.asyncio
async def test_match_refuses_items_automatic_picks_never_air(client, db_session, auth_headers):
    """F-MATCH-12: Match takes only music fit for air, not a jingle, spot or do-not-play song."""
    from app.models.station import Station

    st = Station(id=uuid.uuid4(), name="Web", timezone="UTC")
    db_session.add(st)
    jingle = Asset(id=uuid.uuid4(), title="Station ID", duration=5.0,
                   file_path="https://cdn.example.com/j.mp3", asset_type="jingle")
    banned = Asset(id=uuid.uuid4(), title="Banned", artist="X", duration=200.0, category="do_not_play",
                   file_path="https://cdn.example.com/b.mp3", asset_type="music")
    db_session.add_all([jingle, banned])
    await db_session.commit()
    req = await _approved_request(db_session, st)
    for item in (jingle, banned):
        r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(item.id)}, headers=auth_headers)
        assert r.status_code == 404, (item.title, r.text)


@pytest.mark.asyncio
async def test_match_needs_the_right_to_operate_the_station(client, db_session):
    """F-MATCH-11: matching an approved request puts a song on air, so a manager who may
    not operate the protected (FM) station is refused, exactly as Approve refuses them."""
    from sqlalchemy import select

    from app.models.queue_entry import QueueEntry
    from app.models.station import Station
    from app.models.user import UserRole
    from tests.test_station_permissions import _user

    made = await _library(db_session)
    fm = Station(id=uuid.uuid4(), name="FM", timezone="UTC", protected=True)
    db_session.add(fm)
    await db_session.commit()
    req = await _approved_request(db_session, fm)
    _, manager = await _user(client, db_session, UserRole.MANAGER)
    song = made[("One Day", "Matisyahu")]
    r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(song.id)}, headers=manager)
    assert r.status_code == 403, r.text
    queued = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == fm.id))).scalars().all()
    assert queued == []


@pytest.mark.asyncio
async def test_approving_a_queued_request_again_does_not_queue_it_twice(client, db_session, auth_headers):
    """F-MATCH-23: a second Approve (double tap, two screens) on a queued request is a no-op."""
    from sqlalchemy import select

    from app.models.queue_entry import QueueEntry
    from app.models.song_request import RequestStatus, SongRequest
    from app.models.station import Station

    made = await _library(db_session)
    st = Station(id=uuid.uuid4(), name="Web", timezone="UTC")
    db_session.add(st)
    song = made[("One Day", "Matisyahu")]
    req = SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="Listener", song_title="One Day",
                      asset_id=song.id, status=RequestStatus.PENDING)
    db_session.add(req)
    await db_session.commit()
    for _ in range(2):
        r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"status": "approved"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "queued"
    queued = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == st.id))).scalars().all()
    assert len(queued) == 1
