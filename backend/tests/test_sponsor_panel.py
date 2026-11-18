"""The control room's Sponsor panel (Shmuel 2026-11-18: tapping a sponsorship spot
showed the song panel; "ad click on dashboard should show options for that sponsor's
slots and schedule"): what it reads, and skipping / putting back one coming play."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import UserRole
from app.services.spot_schedule_service import reconcile_spot_slots
from tests.test_checklist_pinned_scheduling import API, _login


async def _setup(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Kol B'ramah Main", timezone="America/New_York", is_active=True)
    other = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    sponsor = Sponsor(id=uuid.uuid4(), name="TEST SPONSOR 27", length_seconds=30, priority=1,
                      audio_file_path="", company_name="Kesher Moving and Storage", contact_phone="732-555-0127")
    db.add_all([station, other, sponsor])
    await db.flush()
    spot = Asset(id=uuid.uuid4(), title="TEST SPONSOR 27 - Kesher Moving", artist="Kol B'ramah",
                 file_path="k.mp3", duration=30.0, asset_type="spot", category="Spots", sponsor_id=sponsor.id)
    song = Asset(id=uuid.uuid4(), title="A song", file_path="s.mp3", duration=200.0, asset_type="music")
    db.add_all([spot, song])
    await db.flush()
    contract = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                            name="Kesher weekday mornings", days_of_week="0,1,2,3,4,5,6",
                            dayparts="morning,afternoon,evening,overnight", plays_per_daypart=1)
    elsewhere = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=other.id,
                             name="Kesher Jerusalem", days_of_week="0,1,2,3,6", dayparts="evening",
                             is_active=False)
    db.add_all([contract, elsewhere])
    await db.commit()
    return station, other, sponsor, spot, song, contract


async def _ad_slots(db: AsyncSession, station, status="pending"):
    station_id = station.id
    db.expire_all()
    rows = (await db.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot", QueueEntry.status == status,
    ))).scalars().all()
    return sorted(rows, key=lambda e: e.preempt_at)


@pytest.mark.asyncio
async def test_panel_shows_sponsor_contracts_and_coming_plays(client: AsyncClient, db_session: AsyncSession):
    station, other, sponsor, spot, _, contract = await _setup(db_session)
    await reconcile_spot_slots(db_session, station.id)
    await db_session.commit()
    _, headers = await _login(client, db_session, UserRole.DJ)

    r = await client.get(f"{API}/spot-schedules/for-asset/{spot.id}", params={"station_id": str(station.id)},
                         headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner"]["company_name"] == "Kesher Moving and Storage"
    names = [(c["name"], c["on_this_station"], c["state"]) for c in body["contracts"]]
    # this station's contract first, the other station's (paused) one after it
    assert names == [("Kesher weekday mornings", True, "running"), ("Kesher Jerusalem", False, "paused")]
    assert body["contracts"][0]["days_text"] == "Every day"
    assert body["contracts"][1]["days_text"] == "Sun-Thu"
    assert body["contracts"][1]["station_name"] == "KBR Jerusalem"
    coming = [e for e in body["today"] + body["upcoming"] if e["status"] == "pending"]
    assert coming, body
    assert all(e["can_skip"] for e in coming if datetime.fromisoformat(e["due"]) > datetime.now(UTC) + timedelta(minutes=3))
    assert len(body["upcoming"]) <= 12


@pytest.mark.asyncio
async def test_skip_one_play_stays_skipped_and_can_be_put_back(client: AsyncClient, db_session: AsyncSession):
    station, *_ = await _setup(db_session)
    station_id = station.id
    await reconcile_spot_slots(db_session, station_id)
    await db_session.commit()
    _, headers = await _login(client, db_session, UserRole.DJ)
    slots = await _ad_slots(db_session, station)
    target = next(e for e in slots if e.preempt_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=10))
    target_id, count = target.id, len(slots)

    r = await client.post(f"{API}/stations/{station_id}/queue/{target_id}/skip-sponsorship", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    # the planner does not put the play back
    await reconcile_spot_slots(db_session, station_id)
    await db_session.commit()
    pending = await _ad_slots(db_session, station)
    assert len(pending) == count - 1
    assert target_id not in {e.id for e in pending}

    # twice is refused with a reason
    r = await client.post(f"{API}/stations/{station_id}/queue/{target_id}/skip-sponsorship", headers=headers)
    assert r.status_code == 409

    r = await client.post(f"{API}/stations/{station_id}/queue/{target_id}/skip-sponsorship",
                          params={"restore": "true"}, headers=headers)
    assert r.status_code == 200, r.text
    await reconcile_spot_slots(db_session, station_id)
    await db_session.commit()
    pending = await _ad_slots(db_session, station)
    assert len(pending) == count and target_id in {e.id for e in pending}


@pytest.mark.asyncio
async def test_skip_refuses_soon_and_non_sponsorship_rows(client: AsyncClient, db_session: AsyncSession):
    station, other, _, spot, song, _ = await _setup(db_session)
    soon = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=1, status="pending",
                      source="ad_slot", preempt_at=datetime.now(UTC) + timedelta(seconds=60))
    plain = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2, status="pending",
                       source="auto")
    db_session.add_all([soon, plain])
    await db_session.commit()
    _, headers = await _login(client, db_session, UserRole.DJ)
    r = await client.post(f"{API}/stations/{station.id}/queue/{soon.id}/skip-sponsorship", headers=headers)
    assert r.status_code == 409 and "2 minutes" in r.json()["detail"]
    r = await client.post(f"{API}/stations/{station.id}/queue/{plain.id}/skip-sponsorship", headers=headers)
    assert r.status_code == 422
    # another station's URL cannot reach this station's row
    r = await client.post(f"{API}/stations/{other.id}/queue/{soon.id}/skip-sponsorship", headers=headers)
    assert r.status_code == 404
    # a viewer may look but not skip
    _, viewer = await _login(client, db_session, UserRole.VIEWER)
    r = await client.get(f"{API}/spot-schedules/for-asset/{spot.id}", params={"station_id": str(station.id)},
                         headers=viewer)
    assert r.status_code == 200
    r = await client.post(f"{API}/stations/{station.id}/queue/{plain.id}/skip-sponsorship", headers=viewer)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sponsors_cannot_read_the_panel(client: AsyncClient, db_session: AsyncSession):
    station, _, _, spot, *_ = await _setup(db_session)
    _, headers = await _login(client, db_session, UserRole.SPONSOR)
    r = await client.get(f"{API}/spot-schedules/for-asset/{spot.id}", params={"station_id": str(station.id)},
                         headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_staff_limited_to_one_station_see_only_that_station(client: AsyncClient, db_session: AsyncSession):
    station, other, _, spot, *_ = await _setup(db_session)
    station_id, other_id, spot_id = station.id, other.id, spot.id
    _, headers = await _login(client, db_session, UserRole.DJ, station_ids=[str(station_id)])
    r = await client.get(f"{API}/spot-schedules/for-asset/{spot_id}", params={"station_id": str(station_id)},
                         headers=headers)
    assert r.status_code == 200, r.text
    assert [c["name"] for c in r.json()["contracts"]] == ["Kesher weekday mornings"]
    assert "KBR Jerusalem" not in r.text
    r = await client.get(f"{API}/spot-schedules/for-asset/{spot_id}", params={"station_id": str(other_id)},
                         headers=headers)
    assert r.status_code == 403
