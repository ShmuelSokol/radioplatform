"""Control room quick action "Do not play" (POST /assets/{id}/do-not-play).

Shmuel, 2026-11-18: seeing a song on air or in the queue, he wants to stop it from
playing again right there, without going to the library.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.asset import Asset, not_placeholder
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.user import User, UserRole
from app.core.security import hash_password
from app.services import playout_engine
from datetime import UTC, datetime, timedelta


def _asset(title, category="Lively"):
    return Asset(id=uuid.uuid4(), title=title, asset_type="music", category=category,
                 duration=180.0, file_path=f"https://cdn.example.com/{uuid.uuid4()}.mp3")


def _entry(station, asset, position, status="pending", source="auto", **kw):
    return QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id,
                      position=position, status=status, source=source, **kw)


async def _setup(db):
    a = Station(id=uuid.uuid4(), name="Quick FM", timezone="America/New_York")
    b = Station(id=uuid.uuid4(), name="Quick Two", timezone="America/New_York")
    song = _asset("Unwanted song")
    other = _asset("Good song")
    db.add_all([a, b, song, other])
    await db.flush()
    rows = {
        "on_air": _entry(a, song, 1, status="playing", started_at=datetime.now(UTC)),
        "other": _entry(a, other, 2),
        "auto": _entry(a, song, 3),
        "manual": _entry(a, song, 4, source="manual"),
        "timed": _entry(a, song, 5, source="block", preempt_at=datetime.now(UTC) + timedelta(hours=1)),
        "played": _entry(a, song, 0, status="played"),
        "b_first": _entry(b, song, 1),
        "b_second": _entry(b, song, 2),
    }
    db.add_all(rows.values())
    await db.commit()
    return a, b, song, other, rows


async def _left(db, ids):
    db.expire_all()
    found = set((await db.execute(select(QueueEntry.id))).scalars().all())
    return {name for name, row_id in ids.items() if row_id in found}


@pytest.mark.asyncio
async def test_do_not_play_marks_the_song_and_clears_waiting_copies(client, auth_headers, db_session):
    a, b, song, other, rows = await _setup(db_session)
    ids = {k: v.id for k, v in rows.items()}
    song_id, other_id = song.id, other.id
    r = await client.post(f"/api/v1/assets/{song_id}/do-not-play", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["do_not_play"] is True and body["category"] == "do_not_play"
    # untimed waiting copies go on both stations; on air, timed, played and other songs stay
    assert body["removed_from_queue"] == 4
    assert body["still_to_play"] == 2
    assert await _left(db_session, ids) == {"on_air", "other", "timed", "played"}
    on_air = await db_session.get(QueueEntry, ids["on_air"])
    assert on_air.status == "playing"
    # no automatic pick may use it any more
    allowed = set((await db_session.execute(select(Asset.id).where(not_placeholder()))).scalars().all())
    assert song_id not in allowed and other_id in allowed


@pytest.mark.asyncio
async def test_copy_held_by_the_engine_is_left(client, auth_headers, db_session, monkeypatch):
    a, b, song, other, rows = await _setup(db_session)
    ids = {k: v.id for k, v in rows.items()}
    b_id = b.id
    monkeypatch.setattr(playout_engine, "held_entry_ids",
                        lambda station_id: {ids["b_first"]} if station_id == b_id else set())
    r = await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers, json={"on": True})
    assert r.status_code == 200, r.text
    assert r.json()["removed_from_queue"] == 3
    assert r.json()["still_to_play"] == 3
    assert "b_first" in await _left(db_session, ids)


@pytest.mark.asyncio
async def test_allow_again_restores_the_old_category(client, auth_headers, db_session):
    a, b, song, other, rows = await _setup(db_session)
    song = type("S", (), {"id": song.id})
    await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers)
    # a second press changes nothing and keeps the remembered category
    again = await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers)
    assert again.json()["removed_from_queue"] == 0
    r = await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers, json={"on": False})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": str(song.id), "category": "Lively", "do_not_play": False,
                        "removed_from_queue": 0, "still_to_play": 0}
    got = await client.get(f"/api/v1/assets/{song.id}", headers=auth_headers)
    assert got.json()["category"] == "Lively"
    assert "category_before_do_not_play" not in (got.json()["metadata_extra"] or {})


@pytest.mark.asyncio
async def test_allow_again_on_a_song_that_was_never_blocked_changes_nothing(client, auth_headers, db_session):
    song = _asset("Fine song", "Relax")
    db_session.add(song)
    song = type("S", (), {"id": song.id})
    await db_session.commit()
    r = await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers, json={"on": False})
    assert r.json()["category"] == "Relax" and r.json()["do_not_play"] is False


@pytest.mark.asyncio
async def test_song_blocked_in_the_library_has_no_old_category(client, auth_headers, db_session):
    song = _asset("Blocked by hand", " DO_NOT_PLAY ")
    db_session.add(song)
    song = type("S", (), {"id": song.id})
    await db_session.commit()
    r = await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers, json={"on": False})
    assert r.json()["category"] is None and r.json()["do_not_play"] is False


@pytest.mark.asyncio
async def test_bad_input_and_unknown_song(client, auth_headers, db_session):
    song = _asset("Any song")
    db_session.add(song)
    song = type("S", (), {"id": song.id})
    await db_session.commit()
    bad = await client.post(f"/api/v1/assets/{song.id}/do-not-play", headers=auth_headers, json={"on": "yes"})
    assert bad.status_code == 422
    missing = await client.post(f"/api/v1/assets/{uuid.uuid4()}/do-not-play", headers=auth_headers)
    assert missing.status_code == 404
    assert (await client.post(f"/api/v1/assets/{song.id}/do-not-play")).status_code in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.DJ, UserRole.VIEWER])
async def test_only_managers_may_block_a_song(client, db_session, role):
    song = _asset("Any song")
    user = User(id=uuid.uuid4(), email=f"{role.value}@example.com",
                hashed_password=hash_password("pw-123456"), role=role, is_active=True)
    song_id, email = song.id, user.email
    db_session.add_all([song, user])
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw-123456"})
    token = login.json()["access_token"]
    r = await client.post(f"/api/v1/assets/{song_id}/do-not-play", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    db_session.expire_all()
    assert (await db_session.get(Asset, song_id)).category == "Lively"
