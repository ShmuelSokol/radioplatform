"""How full each time slot is (the Sponsorship board and the booking window)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_spot_schedules import _setup

pytestmark = pytest.mark.usefixtures("clock_off_the_slots")


@pytest.mark.asyncio
async def test_room_counts_clock_slots_and_booked_plays(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    r = await client.post(
        "/api/v1/spot-schedules",
        json={
            "sponsor_id": sponsors[0],
            "asset_id": str(assets[0].id),
            "station_id": str(station.id),
            "name": "Winter sale",
            "dayparts": "early_morning,late_morning",
            "plays_per_daypart": 2,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text

    r = await client.get(f"/api/v1/spot-schedules/room?station_id={station.id}&days=3", headers=auth_headers)
    assert r.status_code == 200, r.text
    room = r.json()
    assert room["mode"] == "clock" and room["days"] == 3
    assert [s["key"] for s in room["slots"]][:2] == ["early_morning", "late_morning"]
    assert len(room["cells"]) == 3 * len(room["slots"])
    # No blocks: clock slots :15/:30/:45, one sponsorship each, 3 hours a slot
    assert {c["room"] for c in room["cells"] if c["slot"] == "early_afternoon"} == {9}
    # Tomorrow both morning slots hold the contract's 2 plays, nothing elsewhere
    tomorrow = sorted({c["date"] for c in room["cells"]})[1]
    cells = {c["slot"]: c for c in room["cells"] if c["date"] == tomorrow}
    for slot in ("early_morning", "late_morning"):
        assert cells[slot]["booked"] == 2
        [who] = cells[slot]["sponsors"]
        assert (who["sponsor_id"], who["sponsor_name"], who["contract"], who["plays"]) == (
            sponsors[0], "Acme", "Winter sale", 2
        )
    assert cells["early_afternoon"]["booked"] == 0 and not cells["early_afternoon"]["off_air"]


@pytest.mark.asyncio
async def test_room_is_for_managers(client: AsyncClient, db_session: AsyncSession, auth_headers: dict):
    station, _assets, _sponsors = await _setup(db_session, client, auth_headers)
    r = await client.get(f"/api/v1/spot-schedules/room?station_id={station.id}")
    assert r.status_code in (401, 403)
