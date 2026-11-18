"""The Sponsors page as a client database (Shmuel 2026-11-18: "a normal list of
each client, active or not, when they were active; inside each client their spots
through the whole flow, the history of what played and when, and each one's
settings")."""
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_campaign import AdCampaign, CampaignStatus
from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.sponsor import Sponsor
from app.models.spot_recording import SpotRecording
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station

API = "/api/v1"
NY = ZoneInfo("America/New_York")


def _today() -> date:
    return datetime.now(UTC).astimezone(NY).date()


async def _setup(db: AsyncSession):
    main = Station(id=uuid.uuid4(), name="Kol B'ramah Main", timezone="America/New_York", is_active=True)
    jlm = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    db.add_all([main, jlm])
    await db.flush()
    return main, jlm


def _sponsor(db, name, **kw):
    s = Sponsor(id=uuid.uuid4(), name=name, length_seconds=30, audio_file_path="", **kw)
    db.add(s)
    return s


def _asset(db, title, sponsor=None, **kw):
    a = Asset(id=uuid.uuid4(), title=title, file_path=f"{title}.mp3", duration=31.0, asset_type="spot",
              sponsor_id=sponsor.id if sponsor else None, **kw)
    db.add(a)
    return a


def _contract(db, sponsor, asset, station, start, end, **kw):
    c = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=asset.id,
                     station_id=station.id if station else None, start_date=start, end_date=end, **kw)
    db.add(c)
    return c


def _play(db, station, asset, at):
    db.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, start_utc=at,
                   end_utc=at + timedelta(seconds=31), source=PlaySource.AD))


@pytest.mark.asyncio
async def test_client_list_status_and_dates(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    main, _ = await _setup(db_session)
    t = _today()
    running = _sponsor(db_session, "Pita Hut")
    upcoming = _sponsor(db_session, "Bagel Barn")
    ended = _sponsor(db_session, "Old Shoe Store")
    paused = _sponsor(db_session, "Quiet Cafe")
    _sponsor(db_session, "Not Yet Signed")
    await db_session.flush()
    a1 = _asset(db_session, "Pita Hut spot", running)
    a2 = _asset(db_session, "Bagel Barn spot", upcoming)
    a3 = _asset(db_session, "Shoe spot", ended)
    a4 = _asset(db_session, "Cafe spot", paused)
    await db_session.flush()
    _contract(db_session, running, a1, main, t - timedelta(days=10), t + timedelta(days=20))
    _contract(db_session, running, a1, main, t - timedelta(days=40), t - timedelta(days=11))  # older order
    _contract(db_session, upcoming, a2, main, t + timedelta(days=3), None)
    _contract(db_session, ended, a3, main, t - timedelta(days=60), t - timedelta(days=30))
    _contract(db_session, paused, a4, main, t - timedelta(days=5), None, is_active=False)
    now = datetime.now(UTC)
    _play(db_session, main, a1, now - timedelta(days=1))
    _play(db_session, main, a1, now - timedelta(days=2))
    _play(db_session, main, a1, now - timedelta(days=20))
    await db_session.commit()

    r = await client.get(f"{API}/sponsors/clients", headers=auth_headers)
    assert r.status_code == 200
    rows = {c["name"]: c for c in r.json()}
    # alphabetical, every client once
    assert [c["name"] for c in r.json()] == sorted(rows, key=str.lower)
    assert rows["Pita Hut"]["status"] == "running"
    assert rows["Pita Hut"]["active_from"] == (t - timedelta(days=40)).isoformat()
    assert rows["Pita Hut"]["active_until"] == (t + timedelta(days=20)).isoformat()
    assert rows["Pita Hut"]["contracts_running"] == 1 and rows["Pita Hut"]["contracts_total"] == 2
    assert rows["Pita Hut"]["plays_7_days"] == 2 and rows["Pita Hut"]["plays_total"] == 3
    assert rows["Pita Hut"]["spots_on_air"] == 1
    assert rows["Bagel Barn"]["status"] == "upcoming" and rows["Bagel Barn"]["open_ended"] is True
    assert rows["Old Shoe Store"]["status"] == "ended"
    assert rows["Old Shoe Store"]["active_until"] == (t - timedelta(days=30)).isoformat()
    assert rows["Quiet Cafe"]["status"] == "paused"
    assert rows["Not Yet Signed"]["status"] == "none"


@pytest.mark.asyncio
async def test_account_shows_every_spot_in_its_stage(client: AsyncClient, auth_headers: dict,
                                                    db_session: AsyncSession):
    main, _ = await _setup(db_session)
    t = _today()
    sp = _sponsor(db_session, "Pita Hut", contact_name="Moshe", notes="Pays monthly")
    await db_session.flush()
    on_air = _asset(db_session, "Pita Hut - grand opening", sp)
    _asset(db_session, "Pita Hut - old stand-in", sp)
    soon = _asset(db_session, "Pita Hut - Chanukah", sp)
    done = _asset(db_session, "Pita Hut - summer", sp)
    await db_session.flush()
    _contract(db_session, sp, on_air, main, t - timedelta(days=3), t + timedelta(days=3))
    _contract(db_session, sp, soon, main, t + timedelta(days=7), t + timedelta(days=30))
    _contract(db_session, sp, done, main, t - timedelta(days=90), t - timedelta(days=60))
    db_session.add_all([
        SpotRecording(id=uuid.uuid4(), sponsor_id=sp.id, title="Pita Hut - Purim", script_text="Kol B'ramah sponsor Pita Hut...",
                      status="draft", review={"verdict": "ok"}),
        SpotRecording(id=uuid.uuid4(), sponsor_id=sp.id, title="Pita Hut - catering", script_text="Kol B'ramah sponsor...",
                      status="draft", review={"verdict": "needs_changes"}),
        SpotRecording(id=uuid.uuid4(), sponsor_id=sp.id, title="Pita Hut - Pesach", script_text="Kol B'ramah sponsor...",
                      status="ready", duration=29.0),
        SpotRecording(id=uuid.uuid4(), sponsor_id=sp.id, title="Pita Hut - grand opening", script_text="The script on air",
                      status="approved", asset_id=on_air.id),
        AdCampaign(id=uuid.uuid4(), sponsor_id=sp.id, name="Portal request", status=CampaignStatus.IN_REVIEW),
    ])
    _play(db_session, main, on_air, datetime.now(UTC) - timedelta(hours=2))
    await db_session.commit()

    r = await client.get(f"{API}/sponsors/{sp.id}/account", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client"]["contact_name"] == "Moshe" and body["notes"] == "Pays monthly"
    stage = {s["title"]: s["stage"] for s in body["spots"]}
    assert stage == {
        "Pita Hut - grand opening": "on_air",
        "Pita Hut - old stand-in": "ready_not_scheduled",
        "Pita Hut - Chanukah": "starts_soon",
        "Pita Hut - summer": "finished",
        "Pita Hut - Purim": "approved_not_recorded",
        "Pita Hut - catering": "wording",
        "Pita Hut - Pesach": "recorded_needs_approval",
        "Portal request": "wording",
    }
    # the approved recording is shown once, as its library audio, with its script
    grand = next(s for s in body["spots"] if s["title"] == "Pita Hut - grand opening")
    assert grand["script"] == "The script on air" and grand["plays_total"] == 1
    # the spots come in the order of the flow
    order = [st["key"] for st in body["stages"]]
    assert [order.index(s["stage"]) for s in body["spots"]] == sorted(order.index(s["stage"]) for s in body["spots"])
    # contracts: running first, with their settings and plays
    states = [c["state"] for c in body["contracts"]]
    assert states == ["running", "upcoming", "ended"]
    assert body["contracts"][0]["plays_in_contract"] == 1
    assert body["contracts"][0]["station_name"] == "Kol B'ramah Main"
    assert body["contracts"][0]["dayparts"] == "morning,afternoon,evening"


@pytest.mark.asyncio
async def test_contract_whose_recording_is_off_the_air_says_so(client: AsyncClient, auth_headers: dict,
                                                              db_session: AsyncSession):
    main, _ = await _setup(db_session)
    t = _today()
    sp = _sponsor(db_session, "Pita Hut")
    await db_session.flush()
    bad = _asset(db_session, "Pita Hut - broken file", sp, category="do_not_play")
    await db_session.flush()
    _contract(db_session, sp, bad, main, t - timedelta(days=1), t + timedelta(days=5))
    await db_session.commit()
    body = (await client.get(f"{API}/sponsors/{sp.id}/account", headers=auth_headers)).json()
    assert body["contracts"][0]["state"] == "running"
    assert "do not play" in body["contracts"][0]["problem"]
    assert body["spots"][0]["stage"] == "rejected"
    # the list does not count it as a spot on the air
    rows = {c["name"]: c for c in (await client.get(f"{API}/sponsors/clients", headers=auth_headers)).json()}
    assert rows["Pita Hut"]["spots_on_air"] == 0


@pytest.mark.asyncio
async def test_plays_in_contract_count_only_its_station_and_dates(client: AsyncClient, auth_headers: dict,
                                                                  db_session: AsyncSession):
    main, jlm = await _setup(db_session)
    t = _today()
    sp = _sponsor(db_session, "Pita Hut")
    await db_session.flush()
    a = _asset(db_session, "Pita Hut spot", sp)
    await db_session.flush()
    _contract(db_session, sp, a, main, t - timedelta(days=5), t + timedelta(days=5))
    now = datetime.now(UTC)
    _play(db_session, main, a, now - timedelta(days=1))
    _play(db_session, jlm, a, now - timedelta(days=1))  # another station
    _play(db_session, main, a, now - timedelta(days=30))  # before the contract
    await db_session.commit()
    body = (await client.get(f"{API}/sponsors/{sp.id}/account", headers=auth_headers)).json()
    assert body["contracts"][0]["plays_in_contract"] == 1


@pytest.mark.asyncio
async def test_play_history_pages_and_filters(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    main, jlm = await _setup(db_session)
    sp = _sponsor(db_session, "Pita Hut")
    other = _sponsor(db_session, "Someone Else")
    await db_session.flush()
    a = _asset(db_session, "Pita Hut spot", sp)
    b = _asset(db_session, "Pita Hut second spot", sp)
    x = _asset(db_session, "Other spot", other)
    await db_session.flush()
    base = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    for i in range(5):
        _play(db_session, main, a, base + timedelta(hours=i))
    _play(db_session, jlm, b, base + timedelta(days=1))
    _play(db_session, main, x, base)
    await db_session.commit()

    r = (await client.get(f"{API}/sponsors/{sp.id}/plays?limit=4", headers=auth_headers)).json()
    assert r["total"] == 6 and len(r["entries"]) == 4
    assert r["entries"][0]["station_name"] == "KBR Jerusalem"  # newest first
    assert {e["asset_title"] for e in r["entries"]} <= {"Pita Hut spot", "Pita Hut second spot"}
    first = next(e for e in r["entries"] if e["station_name"] == "Kol B'ramah Main")
    assert first["local_date"] == "2026-11-10" and first["local_time"] == "2:00:00 PM"  # 19:00Z, station time
    assert first["how"] == "Sponsorship slot"
    page2 = (await client.get(f"{API}/sponsors/{sp.id}/plays?limit=4&page=2", headers=auth_headers)).json()
    assert len(page2["entries"]) == 2
    only_main = (await client.get(f"{API}/sponsors/{sp.id}/plays?station_id={main.id}", headers=auth_headers)).json()
    assert only_main["total"] == 5
    only_b = (await client.get(f"{API}/sponsors/{sp.id}/plays?asset_id={b.id}", headers=auth_headers)).json()
    assert only_b["total"] == 1
    dated = (await client.get(f"{API}/sponsors/{sp.id}/plays?date_from=2026-11-11&date_to=2026-11-11",
                              headers=auth_headers)).json()
    assert dated["total"] == 1


@pytest.mark.asyncio
async def test_missed_plays_for_staff(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    await _setup(db_session)
    sp = _sponsor(db_session, "Pita Hut")
    await db_session.commit()
    r = await client.get(f"{API}/sponsors/{sp.id}/missed-plays", headers=auth_headers)
    assert r.status_code == 200 and r.json()["rows"] == []
    assert (await client.get(f"{API}/sponsors/{uuid.uuid4()}/account", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_client_with_contracts_is_not_deleted(client: AsyncClient, auth_headers: dict,
                                                   db_session: AsyncSession):
    main, _ = await _setup(db_session)
    sp = _sponsor(db_session, "Pita Hut")
    await db_session.flush()
    a = _asset(db_session, "Pita Hut spot", sp)
    await db_session.flush()
    _contract(db_session, sp, a, main, None, None)
    await db_session.commit()
    r = await client.delete(f"{API}/sponsors/{sp.id}", headers=auth_headers)
    assert r.status_code == 409
    assert "kept" in r.json()["detail"]


@pytest.mark.asyncio
async def test_new_client_needs_only_a_name_and_details(client: AsyncClient, auth_headers: dict):
    r = await client.post(f"{API}/sponsors", json={
        "name": "Kosher Pizza", "company_name": "KP LLC", "contact_name": "Dovid",
        "contact_phone": "732-555-0100", "contact_email": "kp@example.com", "notes": "Wants mornings",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["contact_name"] == "Dovid" and r.json()["notes"] == "Wants mornings"
    rows = (await client.get(f"{API}/sponsors/clients", headers=auth_headers)).json()
    assert rows[0]["name"] == "Kosher Pizza" and rows[0]["status"] == "none"


@pytest.mark.asyncio
async def test_every_client_is_in_the_pickers(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    for i in range(60):
        _sponsor(db_session, f"TEST - Business {i:02d}")
    await db_session.commit()
    rows = (await client.get(f"{API}/sponsors", headers=auth_headers)).json()
    assert len(rows) == 60
