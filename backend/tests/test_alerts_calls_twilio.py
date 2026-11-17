"""Alert notifications and phone/web call-ins (checklist V2-71..V2-80)."""
import time
import uuid
from datetime import UTC, datetime, time as dtime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.alert import AlertSeverity, AlertType
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.station import Station, StationType
from app.models.user import User, UserRole
from app.services import alert_service
from tests.timing import limit


def _user(email, role, phone, prefs, station_ids=None, active=True):
    return User(id=uuid.uuid4(), email=email, hashed_password="x", role=role, is_active=active,
                phone_number=phone, alert_preferences=prefs, station_ids=station_ids)


@pytest.fixture
def texts(monkeypatch):
    sent = []

    async def sms(to, body):
        sent.append(("sms", to, body))
        return True

    async def wa(to, body):
        sent.append(("wa", to, body))
        return True

    monkeypatch.setattr("app.services.sms_service.send_sms", sms)
    monkeypatch.setattr("app.services.sms_service.send_whatsapp", wa)
    return sent


@pytest.mark.asyncio
async def test_alert_texts_go_to_the_right_people(db_session, texts):
    """V2-76: severity preference (default warning), SMS/WhatsApp switches, no
    sponsors, station-limited staff only for their stations."""
    a = Station(id=uuid.uuid4(), name="A", type=StationType.INTERNET)
    b = Station(id=uuid.uuid4(), name="B", type=StationType.INTERNET)
    db_session.add_all([a, b])
    on = {"sms_enabled": True}
    db_session.add_all([
        _user("admin@x.test", UserRole.ADMIN, "+1001", {"sms_enabled": True, "whatsapp_enabled": True}),
        _user("crit@x.test", UserRole.MANAGER, "+1002", {"sms_enabled": True, "min_severity": "critical"}),
        _user("info@x.test", UserRole.MANAGER, "+1003", {"sms_enabled": True, "min_severity": "info"}),
        _user("djb@x.test", UserRole.DJ, "+1004", on, station_ids=[str(b.id)]),
        _user("sponsor@x.test", UserRole.SPONSOR, "+1005", on),
        _user("nophone@x.test", UserRole.ADMIN, None, on),
        _user("off@x.test", UserRole.ADMIN, "+1006", on, active=False),
    ])
    await db_session.commit()

    with patch("app.api.v1.websocket.broadcast_alert", AsyncMock()):
        await alert_service.create_alert(db_session, AlertType.QUEUE_EMPTY, AlertSeverity.WARNING,
                                         "Queue empty", "A ran dry", station_id=a.id)
    got = sorted((k, to) for k, to, _ in texts)
    assert got == [("sms", "+1001"), ("sms", "+1003"), ("wa", "+1001")]
    assert texts[0][2].startswith("[WARNING] Queue empty\nA ran dry")

    texts.clear()
    with patch("app.api.v1.websocket.broadcast_alert", AsyncMock()):
        await alert_service.create_alert(db_session, AlertType.SYSTEM, AlertSeverity.INFO, "Restarted", "hello")
    assert sorted(to for _, to, _ in texts) == ["+1003"]


@pytest.mark.xfail(strict=True, reason="BUG: a repeat that escalates an open alert (warning -> critical) texts and pushes nobody; "
                   "tests/test_alert_repeats.py currently asserts that, so the owner must decide")
@pytest.mark.asyncio
async def test_escalation_notifies_again(db_session, texts):
    """V2-77: a repeat that raises the severity (warning -> critical) texts and pushes again."""
    st = Station(id=uuid.uuid4(), name="A", type=StationType.INTERNET)
    db_session.add(st)
    db_session.add(_user("crit@x.test", UserRole.MANAGER, "+1002", {"sms_enabled": True, "min_severity": "critical"}))
    await db_session.commit()
    push = AsyncMock()
    with patch("app.api.v1.websocket.broadcast_alert", push):
        first = await alert_service.create_alert(db_session, AlertType.SILENCE, AlertSeverity.WARNING, "Dead air", "10 s", station_id=st.id)
        assert texts == [] and push.await_count == 1
        same = await alert_service.create_alert(db_session, AlertType.SILENCE, AlertSeverity.WARNING, "Dead air", "20 s", station_id=st.id)
        assert texts == [] and push.await_count == 1  # plain repeat: quiet
        worse = await alert_service.create_alert(db_session, AlertType.SILENCE, AlertSeverity.CRITICAL, "Dead air", "60 s", station_id=st.id)
    assert first.id == same.id == worse.id and worse.severity == AlertSeverity.CRITICAL
    assert [to for _, to, _ in texts] == ["+1002"]
    assert push.await_count == 2


@pytest.mark.asyncio
async def test_alert_filter_values(client, auth_headers, db_session):
    """V2-79: unknown severity/type -> 422; values are case-insensitive."""
    with patch("app.services.alert_service._notify_users", AsyncMock()), \
            patch("app.api.v1.websocket.broadcast_alert", AsyncMock()):
        await alert_service.create_alert(db_session, AlertType.QUEUE_EMPTY, AlertSeverity.WARNING, "x", "y")
    await db_session.commit()
    assert (await client.get("/api/v1/alerts", params={"severity": "urgent"}, headers=auth_headers)).status_code == 422
    assert (await client.get("/api/v1/alerts", params={"alert_type": "nope"}, headers=auth_headers)).status_code == 422
    t0 = time.perf_counter()
    r = await client.get("/api/v1/alerts", params={"severity": "WARNING", "alert_type": "Queue_Empty"}, headers=auth_headers)
    assert time.perf_counter() - t0 < limit(0.3)
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_schedule_conflict_alert(db_session):
    """V2-80: overlapping blocks in one schedule raise "Schedule conflict: A vs B";
    start-only (until next) blocks never conflict."""
    st = Station(id=uuid.uuid4(), name="S", type=StationType.INTERNET)
    db_session.add(st)
    await db_session.commit()
    sch = Schedule(id=uuid.uuid4(), station_id=st.id, name="Main")
    db_session.add(sch)
    await db_session.commit()
    morning = ScheduleBlock(id=uuid.uuid4(), schedule_id=sch.id, name="Morning", start_time=dtime(6), end_time=dtime(10))
    late = ScheduleBlock(id=uuid.uuid4(), schedule_id=sch.id, name="Late morning", start_time=dtime(9), end_time=dtime(12))
    noon = ScheduleBlock(id=uuid.uuid4(), schedule_id=sch.id, name="Noon", start_time=dtime(12), end_time=dtime(13))
    handover = ScheduleBlock(id=uuid.uuid4(), schedule_id=sch.id, name="Handover", start_time=dtime(7), end_time=dtime(8), until_next=True)
    db_session.add_all([morning, late, noon, handover])
    await db_session.commit()
    with patch("app.services.alert_service._notify_users", AsyncMock()), \
            patch("app.api.v1.websocket.broadcast_alert", AsyncMock()):
        found = await alert_service.detect_schedule_conflicts(db_session, sch.id, morning.id)
        assert [a.title for a in found] == ["Schedule conflict: Morning vs Late morning"]
        assert await alert_service.detect_schedule_conflicts(db_session, sch.id, noon.id) == []
        assert await alert_service.detect_schedule_conflicts(db_session, sch.id, handover.id) == []


# --- Phone call-ins (Twilio webhooks) ---

async def _show(db, station, calls=True, status=LiveShowStatus.LIVE, title="Show"):
    s = LiveShow(id=uuid.uuid4(), station_id=station.id, title=title, status=status, calls_enabled=calls)
    db.add(s)
    await db.commit()
    return s


@pytest.mark.asyncio
async def test_phone_call_prompts_and_hang_ups(client, db_session):
    """V2-73/V2-72: hold prompt and music; no show -> sorry and hang up; a caller who
    hangs up while waiting is abandoned, while on air completed with air_end."""
    from app.config import settings

    st = Station(id=uuid.uuid4(), name="S", type=StationType.INTERNET)
    db_session.add(st)
    await db_session.commit()

    r = await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+15551234567", "CallSid": "CA0"})
    assert "Sorry, there is no live show accepting calls" in r.text and "<Hangup/>" in r.text

    show = await _show(db_session, st)
    with patch.object(settings, "LIVE_SHOW_HOLD_MUSIC_URL", "https://cdn.test/hold.mp3"):
        r = await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+15551234567", "CallSid": "CA1"})
    assert "Thank you for calling in. Please hold" in r.text and 'waitUrl="https://cdn.test/hold.mp3"' in r.text
    assert f"hold-{show.id}" in r.text
    r = await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+15559999999", "CallSid": "CA2"})
    assert "BusssyBoy.mp3" in r.text  # Twilio's sample when no hold music is set

    calls = {c.twilio_call_sid: c for c in (await db_session.execute(select(CallInRequest))).scalars()}
    assert calls["CA1"].status == CallStatus.WAITING and calls["CA1"].caller_phone == "+15551234567"
    calls["CA2"].status = CallStatus.ON_AIR
    calls["CA2"].air_start = datetime.now(UTC)
    await db_session.commit()

    for sid in ("CA1", "CA2"):
        r = await client.post("/api/v1/live-shows/twilio/status", data={"CallSid": sid, "CallStatus": "completed"})
        assert r.status_code == 200
    for c in calls.values():
        await db_session.refresh(c)
    assert calls["CA1"].status == CallStatus.ABANDONED
    assert calls["CA2"].status == CallStatus.COMPLETED and calls["CA2"].air_end is not None


@pytest.mark.asyncio
async def test_phone_call_reaches_the_dialled_station(client, db_session):
    from app.config import settings

    a = Station(id=uuid.uuid4(), name="A", type=StationType.INTERNET)
    b = Station(id=uuid.uuid4(), name="B", type=StationType.INTERNET, broadcast_config={"call_in_number": "+15550000002"})
    db_session.add_all([a, b])
    await db_session.commit()
    await _show(db_session, a, title="A show")
    show_b = await _show(db_session, b, title="B show")
    with patch.object(settings, "TWILIO_VOICE_NUMBER", "+15550000001"):
        await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+1555", "CallSid": "CAx", "To": "+15550000002"})
    call = (await db_session.execute(select(CallInRequest))).scalar_one()
    assert call.live_show_id == show_b.id


@pytest.mark.asyncio
async def test_web_call_in_refusals(client, db_session):
    """V2-75: no show 409, show not taking calls 409, name limits 422, 5 per address per 10 min."""
    from app.api.v1 import call_in

    call_in._recent.clear()
    st = Station(id=uuid.uuid4(), name="S", type=StationType.INTERNET)
    db_session.add(st)
    await db_session.commit()
    url = f"/api/v1/call-in/{st.id}"
    hdr = {"X-Forwarded-For": "203.0.113.9"}

    r = await client.post(url, json={"name": "Chaim"}, headers=hdr)
    assert r.status_code == 409 and r.json()["detail"] == "There is no live show on this station right now."
    show = await _show(db_session, st, calls=False)
    r = await client.post(url, json={"name": "Chaim"}, headers=hdr)
    assert r.status_code == 409 and r.json()["detail"] == "This show is not taking calls."
    show.calls_enabled = True
    await db_session.commit()

    for body in ({"name": ""}, {"name": "x" * 101}, {"name": "ok", "note": "n" * 301}):
        assert (await client.post(url, json=body, headers={"X-Forwarded-For": "198.51.100.1"})).status_code == 422
    call_in._recent.clear()
    with patch("app.api.v1.live_shows_ws.broadcast_show_event", AsyncMock()):
        codes = [(await client.post(url, json={"name": f"C{i}"}, headers=hdr)).status_code for i in range(6)]
    assert codes == [201] * 5 + [429]
    call_in._recent.clear()

