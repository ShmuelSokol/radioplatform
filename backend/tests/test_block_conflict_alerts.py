"""A schedule block saved over another one raises a schedule_conflict alert.

Row: B-JOB-83."""
import uuid

import pytest
from sqlalchemy import select

from app.models.alert import Alert, AlertType
from app.models.schedule import Schedule
from app.models.station import Station


async def _schedule(db):
    st = Station(id=uuid.uuid4(), name=f"C{uuid.uuid4().hex[:5]}", timezone="UTC")
    db.add(st)
    await db.flush()
    sc = Schedule(id=uuid.uuid4(), station_id=st.id, name="Week")
    db.add(sc)
    await db.commit()
    return sc


async def _block(client, headers, schedule, name, start, end, **kw):
    r = await client.post("/api/v1/schedules/blocks", headers=headers, json={
        "schedule_id": str(schedule.id), "name": name, "start_time": start, "end_time": end, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _conflicts(db):
    rows = (await db.execute(
        select(Alert).where(Alert.alert_type == AlertType.SCHEDULE_CONFLICT).execution_options(populate_existing=True)
    )).scalars().all()
    return sorted(a.title for a in rows)


@pytest.mark.asyncio
async def test_overlapping_block_raises_a_conflict_alert(client, auth_headers, db_session):
    """B-JOB-83: a new block overlapping another raises one warning naming both; blocks that only
    touch do not; editing a block into an overlap raises one too; start-only blocks never conflict."""
    sc = await _schedule(db_session)
    await _block(client, auth_headers, sc, "Morning", "06:00:00", "10:00:00")
    await _block(client, auth_headers, sc, "Late morning", "10:00:00", "12:00:00")      # touches only
    assert await _conflicts(db_session) == []

    await _block(client, auth_headers, sc, "Shiur", "09:00:00", "09:30:00")
    assert await _conflicts(db_session) == ["Schedule conflict: Shiur vs Morning"]

    evening = await _block(client, auth_headers, sc, "Evening", "18:00:00", "20:00:00")
    r = await client.patch(f"/api/v1/schedules/blocks/{evening['id']}", headers=auth_headers,
                           json={"start_time": "11:00:00"})
    assert r.status_code == 200, r.text
    assert "Schedule conflict: Evening vs Late morning" in await _conflicts(db_session)

    before = await _conflicts(db_session)
    await _block(client, auth_headers, sc, "Handover", "09:15:00", "09:15:00", until_next=True)
    assert await _conflicts(db_session) == before
    alert = (await db_session.execute(select(Alert).where(Alert.title == "Schedule conflict: Shiur vs Morning"))).scalar_one()
    assert alert.severity.value == "warning" and alert.context["schedule_id"] == str(sc.id)


@pytest.mark.asyncio
async def test_blocks_on_different_days_do_not_conflict(client, auth_headers, db_session):
    """B-JOB-83: weekly blocks on different days at the same time are not a conflict."""
    sc = await _schedule(db_session)
    await _block(client, auth_headers, sc, "Monday show", "20:00:00", "21:00:00",
                 recurrence_type="weekly", recurrence_pattern=["monday"])
    await _block(client, auth_headers, sc, "Tuesday show", "20:00:00", "21:00:00",
                 recurrence_type="weekly", recurrence_pattern=["tuesday"])
    assert await _conflicts(db_session) == []


@pytest.mark.asyncio
async def test_overnight_block_conflict_is_found(client, auth_headers, db_session):
    """B-JOB-83: a block inside an overnight block is a conflict."""
    sc = await _schedule(db_session)
    await _block(client, auth_headers, sc, "Overnight", "22:00:00", "02:00:00")
    await _block(client, auth_headers, sc, "Late news", "23:00:00", "23:30:00")
    assert await _conflicts(db_session) == ["Schedule conflict: Late news vs Overnight"]


def test_recurring_blocks_with_separate_date_ranges_do_not_conflict():
    """B-PUBLIC-5 follow-on: two daily blocks at the same hour in different seasons
    never air together; touching or open-ended ranges still do."""
    from datetime import date, time
    from types import SimpleNamespace

    from app.services.alert_service import blocks_overlap

    def blk(start=None, end=None, s=time(9), e=time(10)):
        return SimpleNamespace(recurrence_type="daily", recurrence_pattern=None,
                               start_time=s, end_time=e, start_date=start, end_date=end)

    summer = blk(date(2026, 6, 1), date(2026, 8, 31))
    winter = blk(date(2026, 12, 1), date(2027, 2, 28))
    assert not blocks_overlap(summer, winter)
    assert blocks_overlap(summer, blk(date(2026, 8, 31)))       # shares the last day
    assert blocks_overlap(summer, blk())                        # open-ended
    assert not blocks_overlap(summer, blk(None, date(2026, 5, 31)))
    # overnight on the last day reaches into the next day's 00:30
    late = blk(None, date(2026, 5, 31), time(23), time(1))
    assert blocks_overlap(late, blk(date(2026, 6, 1), None, time(0), time(0, 45)))
