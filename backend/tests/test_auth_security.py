"""Endpoints with no staff login, sign-in protections and account safety rails.

B-ROLE-14/17, B-AUTH-11..13/16, B-MAIN-2, B-SET-40, B-BILLING-5/6, B-LIVESHOWS-20..23,
V1-103..V1-107. Credentials here are made up.
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_access_token, decode_token, hash_password
from app.models.user import User, UserRole
from tests.timing import limit


async def _login(db, role: UserRole, **kw) -> tuple[User, dict]:
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=True, **kw)
    db.add(u)
    await db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


# ---------- unguarded maintenance endpoints ----------

@pytest.mark.asyncio
async def test_migrate_is_admin_only(client: AsyncClient, db_session: AsyncSession):
    assert (await client.post("/api/v1/auth/migrate")).status_code in (401, 403)
    _, viewer = await _login(db_session, UserRole.VIEWER)
    assert (await client.post("/api/v1/auth/migrate", headers=viewer)).status_code == 403
    _, admin = await _login(db_session, UserRole.ADMIN)
    r = await client.post("/api/v1/auth/migrate", headers=admin)
    assert r.status_code == 200
    # safe to repeat
    assert (await client.post("/api/v1/auth/migrate", headers=admin)).status_code == 200


@pytest.mark.asyncio
async def test_init_is_harmless_to_repeat(client: AsyncClient, db_session: AsyncSession):
    """GET /api/v1/init stays public (Vercel compat). It only creates missing tables
    once per process and returns no data, so repeating it costs nothing."""
    _, admin = await _login(db_session, UserRole.ADMIN)
    for _ in range(3):
        started = time.perf_counter()
        r = await client.get("/api/v1/init")
        assert r.status_code == 200
        assert set(r.json()) == {"status", "created"}
        assert time.perf_counter() - started < limit(0.3)
    # nothing was lost
    assert (await client.get("/api/v1/auth/me", headers=admin)).status_code == 200


@pytest.mark.asyncio
async def test_seed_is_refused_once_an_admin_exists(client: AsyncClient, db_session: AsyncSession):
    await _login(db_session, UserRole.ADMIN)
    r = await client.post("/api/v1/auth/seed")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_seed_reset(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """V1-103: resets the first admin's login to "admin" with ADMIN_SEED_PASSWORD."""
    first, admin = await _login(db_session, UserRole.ADMIN)
    monkeypatch.setattr(settings, "ADMIN_SEED_PASSWORD", "")
    assert (await client.post("/api/v1/auth/seed-reset", headers=admin)).status_code == 503
    monkeypatch.setattr(settings, "ADMIN_SEED_PASSWORD", "made-up-seed-pw")
    first.is_active = True
    await db_session.commit()
    r = await client.post("/api/v1/auth/seed-reset", headers=admin)
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/auth/login", json={"email": "admin", "password": "made-up-seed-pw"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_seed_reset_with_two_admins_does_not_crash(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_SEED_PASSWORD", "made-up-seed-pw")
    first, admin = await _login(db_session, UserRole.ADMIN)
    first.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    first.is_active = False  # V1-103: the first admin is switched back on
    second, second_headers = await _login(db_session, UserRole.ADMIN)
    await db_session.commit()
    r = await client.post("/api/v1/auth/seed-reset", headers=second_headers)
    assert r.status_code == 200, r.text
    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.email == "admin" and second.email != "admin"
    assert first.is_active is True
    # the 404 ("no admin") branch cannot be reached: only a signed-in active admin may call it


@pytest.mark.asyncio
async def test_seed_assets_and_reset_refused_for_managers(client: AsyncClient, db_session: AsyncSession):
    _, manager = await _login(db_session, UserRole.MANAGER)
    for path in ("/api/v1/auth/seed-assets", "/api/v1/auth/seed-reset", "/api/v1/auth/seed-all"):
        assert (await client.post(path, headers=manager)).status_code == 403


# ---------- sign-in ----------

@pytest.mark.asyncio
async def test_failed_sign_in_is_logged_against_the_known_user(client: AsyncClient, db_session: AsyncSession):
    """V1-105"""
    from app.models.audit_log import AuditLog

    u = User(id=uuid.uuid4(), email="Known.Person@t.com", hashed_password=hash_password("right-pw-123"),
             role=UserRole.DJ, is_active=True)
    db_session.add(u)
    await db_session.commit()
    r = await client.post("/api/v1/auth/login", json={"email": "known.person@T.com", "password": "wrong"})
    assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={"email": "nobody@t.com", "password": "wrong"})
    assert r.status_code == 401
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.label == "Sign-in failed"))).scalars().all()
    by_email = {row.user_email: row for row in rows}
    assert by_email["known.person@T.com"].user_id == u.id
    assert by_email["nobody@t.com"].user_id is None


@pytest.mark.asyncio
async def test_login_is_rate_limited_per_address(client: AsyncClient, db_session: AsyncSession):
    """10 sign-ins a minute from one address, then 429."""
    codes = []
    for _ in range(11):
        r = await client.post("/api/v1/auth/login", json={"email": "x@t.com", "password": "nope"})
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


@pytest.mark.asyncio
async def test_login_limit_counts_each_real_client_separately(client: AsyncClient, db_session: AsyncSession):
    for i in range(11):
        r = await client.post("/api/v1/auth/login", json={"email": "x@t.com", "password": "nope"},
                              headers={"X-Forwarded-For": f"203.0.113.{i + 1}"})
        assert r.status_code == 401, f"operator {i + 1} was refused with {r.status_code}"


def test_production_refuses_the_default_jwt_secret():
    from app.config import Settings

    with pytest.raises(Exception):
        Settings(APP_ENV="production", JWT_SECRET_KEY="change-me-in-production")


@pytest.mark.asyncio
async def test_token_lifetimes(client: AsyncClient, db_session: AsyncSession):
    """B-SET-40: access tokens live 30 minutes, refresh tokens 7 days; an expired token is refused."""
    import jwt as pyjwt

    u = User(id=uuid.uuid4(), email="life@t.com", hashed_password=hash_password("pw-123456"),
             role=UserRole.ADMIN, is_active=True)
    db_session.add(u)
    await db_session.commit()
    r = await client.post("/api/v1/auth/login", json={"email": "life@t.com", "password": "pw-123456"})
    tokens = r.json()
    now = time.time()
    access, refresh = decode_token(tokens["access_token"]), decode_token(tokens["refresh_token"])
    assert abs(access["exp"] - now - 30 * 60) < 60
    assert abs(refresh["exp"] - now - 7 * 86400) < 60
    expired = pyjwt.encode({"sub": str(u.id), "type": "access", "exp": int(now) - 5},
                           settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


# ---------- admin safety rails (B-ROLE-14, B-USERS-6/8) ----------

@pytest.mark.asyncio
async def test_admin_cannot_lock_themselves_out(client: AsyncClient, db_session: AsyncSession):
    me, admin = await _login(db_session, UserRole.ADMIN)
    r = await client.put(f"/api/v1/users/{me.id}", json={"is_active": False}, headers=admin)
    assert r.status_code == 400
    r = await client.put(f"/api/v1/users/{me.id}", json={"role": "manager"}, headers=admin)
    assert r.status_code == 400
    r = await client.delete(f"/api/v1/users/{me.id}", headers=admin)
    assert r.status_code == 400
    await db_session.refresh(me)
    assert me.is_active and me.role == UserRole.ADMIN
    # another admin can be changed
    other, _ = await _login(db_session, UserRole.ADMIN)
    r = await client.put(f"/api/v1/users/{other.id}", json={"role": "manager"}, headers=admin)
    assert r.status_code == 200 and r.json()["role"] == "manager"


# ---------- Stripe webhook (B-BILLING-5/6) ----------

def _stripe_header(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


async def _invoice(db):
    from app.models.invoice import Invoice, InvoiceStatus
    from app.models.sponsor import Sponsor

    sp = Sponsor(id=uuid.uuid4(), name="Shop", length_seconds=30, audio_file_path="x.mp3")
    db.add(sp)
    await db.flush()
    inv = Invoice(id=uuid.uuid4(), sponsor_id=sp.id, amount_cents=5000, status=InvoiceStatus.SENT)
    db.add(inv)
    await db.commit()
    return inv


@pytest.mark.asyncio
async def test_stripe_webhook_off_when_stripe_not_configured(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    r = await client.post("/api/v1/billing/webhook", content=b"{}")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_stripe_webhook_checks_the_signature(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    from app.models.invoice import InvoiceStatus

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_made_up")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_made_up")
    inv = await _invoice(db_session)
    event = {"id": "evt_1", "object": "event", "type": "checkout.session.completed",
             "data": {"object": {"object": "checkout.session", "metadata": {"invoice_id": str(inv.id)},
                                 "payment_intent": "pi_123"}}}
    payload = json.dumps(event).encode()

    # forged: no signature, or signed with another secret
    for header in ("", _stripe_header(payload, "whsec_someone_else")):
        r = await client.post("/api/v1/billing/webhook", content=payload, headers={"stripe-signature": header})
        assert r.status_code == 400, r.text
    await db_session.refresh(inv)
    assert inv.status == InvoiceStatus.SENT

    r = await client.post("/api/v1/billing/webhook", content=payload,
                          headers={"stripe-signature": _stripe_header(payload, "whsec_made_up")})
    assert r.status_code == 200, r.text
    await db_session.refresh(inv)
    assert inv.status == InvoiceStatus.PAID and inv.stripe_payment_intent_id == "pi_123"


# ---------- Twilio webhooks (B-LIVESHOWS-20..23) ----------

async def _live_show(db):
    from app.models.live_show import LiveShow, LiveShowStatus
    from app.models.station import Station

    st = Station(id=uuid.uuid4(), name="Live", timezone="UTC")
    db.add(st)
    await db.flush()
    show = LiveShow(id=uuid.uuid4(), station_id=st.id, title="Call-in", status=LiveShowStatus.LIVE,
                    calls_enabled=True, actual_start=datetime.now(timezone.utc))
    db.add(show)
    await db.commit()
    return show


@pytest.mark.asyncio
async def test_twilio_inbound_with_no_live_show_answers_politely(client: AsyncClient, db_session: AsyncSession):
    r = await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+15550000000", "CallSid": "CA1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<Response" in r.text


@pytest.mark.asyncio
async def test_twilio_inbound_queues_the_caller_and_status_ends_it(client: AsyncClient, db_session: AsyncSession):
    from app.models.call_in_request import CallInRequest, CallStatus

    show = await _live_show(db_session)
    r = await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+15550000001", "CallSid": "CA2"})
    assert r.status_code == 200
    call = (await db_session.execute(select(CallInRequest))).scalar_one()
    assert call.live_show_id == show.id and call.status == CallStatus.WAITING
    r = await client.post("/api/v1/live-shows/twilio/status", data={"CallSid": "CA2", "CallStatus": "completed"})
    assert r.status_code == 200
    await db_session.refresh(call)
    assert call.status == CallStatus.ABANDONED


@pytest.mark.asyncio
async def test_twilio_hold_music_is_twiml(client: AsyncClient):
    r = await client.get("/api/v1/live-shows/twilio/hold-music")
    assert r.status_code == 200 and "<Response" in r.text


@pytest.mark.asyncio
async def test_unsigned_twilio_webhook_is_refused(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """B-LIVESHOWS-21 (fixed): with TWILIO_AUTH_TOKEN set, a webhook without a valid
    X-Twilio-Signature gets 403 and changes nothing; a correctly signed one works."""
    from twilio.request_validator import RequestValidator

    from app.api.v1.live_shows import twilio_signature
    from app.config import settings
    from app.models.call_in_request import CallInRequest, CallStatus

    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tw-secret")
    monkeypatch.setattr(settings, "BACKEND_PUBLIC_URL", "https://api.example.test")
    await _live_show(db_session)
    form = {"From": "+15550000002", "CallSid": "CAx", "To": "+15550009999"}
    wrong_key = twilio_signature("other", "http://test/api/v1/live-shows/twilio/inbound", form.items())
    for headers in ({}, {"X-Twilio-Signature": "bm9wZQ=="}, {"X-Twilio-Signature": wrong_key}):
        r = await client.post("/api/v1/live-shows/twilio/inbound", data=form, headers=headers)
        assert r.status_code == 403, r.text
    assert (await db_session.execute(select(CallInRequest))).scalars().all() == []

    # Our signature is Twilio's own algorithm
    public = "https://api.example.test/api/v1/live-shows/twilio/inbound"
    assert twilio_signature("tw-secret", public, form.items()) == \
        RequestValidator("tw-secret").compute_signature(public, form)

    # Signed for the public URL Twilio was given (the app sees another host behind the proxy)
    r = await client.post("/api/v1/live-shows/twilio/inbound", data=form,
                          headers={"X-Twilio-Signature": twilio_signature("tw-secret", public, form.items())})
    assert r.status_code == 200 and "<Response" in r.text
    call = (await db_session.execute(select(CallInRequest))).scalar_one()
    assert call.status == CallStatus.WAITING

    # The status callback is checked too
    status_form = {"CallSid": "CAx", "CallStatus": "completed"}
    r = await client.post("/api/v1/live-shows/twilio/status", data=status_form)
    assert r.status_code == 403
    await db_session.refresh(call)
    assert call.status == CallStatus.WAITING
    status_url = "https://api.example.test/api/v1/live-shows/twilio/status"
    r = await client.post("/api/v1/live-shows/twilio/status", data=status_form,
                          headers={"X-Twilio-Signature": twilio_signature("tw-secret", status_url, status_form.items())})
    assert r.status_code == 200
    await db_session.refresh(call)
    assert call.status == CallStatus.ABANDONED
