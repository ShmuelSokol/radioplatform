"""Regenerating Shabbos / Yom Tov windows corrects stored ones whose computed times changed.

Found live 2026-11-19: Jerusalem's candle lighting went from 18 to 40 minutes before
shekia (PR 400), but regenerate skipped every window that already had a same-named one
within an hour, so all 58 stored Jerusalem windows kept the start 22 minutes late.
"""
import uuid
from datetime import UTC, datetime, timedelta

from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.services import shabbos_service
from app.services.station_service import auto_generate_holidays_for_station
from app.services.sun_service import get_sun_times

JERUSALEM = (31.7683, 35.2137, "Asia/Jerusalem")


async def _station(db, name="KBR Jerusalem"):
    lat, lon, tz = JERUSALEM
    st = Station(id=uuid.uuid4(), name=name, timezone=tz, latitude=lat, longitude=lon, is_active=True)
    db.add(st)
    await db.commit()
    return st


def _aware(t):
    # SQLite hands back the clock time the generator wrote (Jerusalem time), with no zone
    return t if t.tzinfo else t.replace(tzinfo=ZoneInfo(JERUSALEM[2]))


async def _rows(db):
    db.expire_all()
    rows = (await db.execute(select(HolidayWindow).order_by(HolidayWindow.start_datetime))).scalars().all()
    return rows


def _minutes_before_shekia(row) -> float:
    lat, lon, tz = JERUSALEM
    start = _aware(row.start_datetime)
    day = start.astimezone(ZoneInfo(tz)).date()
    return (get_sun_times(lat, lon, tz, day)["sunset"] - start).total_seconds() / 60


@pytest.mark.asyncio
async def test_a_changed_candle_lighting_rule_corrects_the_stored_windows(client, auth_headers, db_session, monkeypatch):
    st = await _station(db_session)
    sid = str(st.id)
    # the windows as they were stored under the old rule: 18 minutes in Jerusalem too
    monkeypatch.setattr(shabbos_service, "CANDLE_LIGHTING_MINUTES_ISRAEL", 18)
    made = await auto_generate_holidays_for_station(db_session, st, months_ahead=3)
    await db_session.commit()
    before = await _rows(db_session)
    assert made == len(before) >= 12
    now = datetime.now(UTC)
    # a window whose corrected start (22 minutes earlier) is still ahead can be moved; one
    # that has begun, or would have begun by the new rule, is left as it is
    future = [r for r in before if _aware(r.start_datetime) - timedelta(minutes=23) > now]
    assert all(abs(_minutes_before_shekia(r) - 18) < 0.1 for r in future)
    ends = {r.id: _aware(r.end_datetime) for r in before}
    past_starts = {r.id: _aware(r.start_datetime) for r in before if r not in future}

    # one future window was moved by hand, and one is shared with a second station
    by_hand, shared = future[1].id, future[2].id
    hand_start = _aware(future[1].start_datetime) - timedelta(minutes=7)
    shared_start = _aware(future[2].start_datetime)
    made_1, made_2 = future[1].created_at, future[2].created_at
    await db_session.execute(update(HolidayWindow).where(HolidayWindow.id == by_hand).values(
        start_datetime=hand_start, updated_at=made_1 + timedelta(hours=2)))
    await db_session.execute(update(HolidayWindow).where(HolidayWindow.id == shared).values(
        affected_stations={"station_ids": [sid, str(uuid.uuid4())]}, updated_at=made_2))
    await db_session.commit()

    # the rule is now 40 minutes: regenerate through the page's own call
    monkeypatch.setattr(shabbos_service, "CANDLE_LIGHTING_MINUTES_ISRAEL", 40)
    r = await client.post("/api/v1/holidays/auto-generate", json={"station_id": sid, "months_ahead": 3},
                          headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    after = await _rows(db_session)
    assert len(after) == len(before), "no window twice"
    assert body["created"] == 0
    assert body["updated"] == len(future) - 2
    assert body["skipped"] == len(before) - body["updated"]
    for row in after:
        start = _aware(row.start_datetime)
        if row.id == by_hand:
            assert start == hand_start, "a window edited by hand is left alone"
        elif row.id == shared:
            assert start == shared_start, "a window shared with another station is left alone"
        elif row.id in past_starts:
            assert start == past_starts[row.id], "a Shabbos that already began is not moved"
        else:
            assert abs(_minutes_before_shekia(row) - 40) < 0.1
        assert _aware(row.end_datetime) == ends[row.id]  # shekia + 72 did not change

    # again with nothing changed: nothing made, nothing moved
    r = await client.post("/api/v1/holidays/auto-generate", json={"station_id": sid, "months_ahead": 3},
                          headers=auth_headers)
    assert r.json() == {"created": 0, "skipped": len(before), "updated": 0}

    # and the rule changing once more still corrects the same rows (they stay the generator's)
    monkeypatch.setattr(shabbos_service, "CANDLE_LIGHTING_MINUTES_ISRAEL", 30)
    st = await db_session.get(Station, uuid.UUID(sid))
    made = await auto_generate_holidays_for_station(db_session, st, months_ahead=3)
    await db_session.commit()
    assert made == 0
    moved = [r for r in await _rows(db_session) if r.id not in past_starts and r.id not in (by_hand, shared)]
    assert moved and all(abs(_minutes_before_shekia(r) - 30) < 0.1 for r in moved)
