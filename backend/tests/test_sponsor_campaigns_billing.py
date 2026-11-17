"""Sponsor campaigns, drafts, comments, invoices, Stripe and the emails they send
(checklist V2-57..V2-69)."""
import json
import time
import types
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio

from app.core.security import hash_password
from app.models.ad_campaign import AdCampaign
from app.models.asset import Asset
from app.models.invoice import Invoice, InvoiceStatus
from app.models.play_log import PlayLog, PlaySource
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.station import Station, StationType
from app.models.user import User, UserRole
from tests.timing import limit

API = "/api/v1"


@pytest.fixture
def mails(monkeypatch):
    """Every email the backend tries to send: (to, subject, html)."""
    sent: list[tuple[str, str, str]] = []

    async def fake_send(to, subject, html_body):
        sent.append((to, subject, html_body))
        return True

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    monkeypatch.setattr("app.api.v1.ai_emails.send_email", fake_send)
    return sent


@pytest_asyncio.fixture
async def sponsor_login(client, db_session):
    user = User(
        id=uuid.uuid4(), email="acme@sponsor.test", hashed_password=hash_password("sponsorpw1"),
        role=UserRole.SPONSOR, is_active=True, display_name="Acme Owner",
    )
    db_session.add(user)
    sponsor = Sponsor(
        id=uuid.uuid4(), name="Acme", length_seconds=30, audio_file_path="spots/acme.mp3",
        user_id=user.id, contact_email="owner@acme.test",
    )
    db_session.add(sponsor)
    await db_session.commit()
    r = await client.post(f"{API}/auth/login", json={"email": user.email, "password": "sponsorpw1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, sponsor, user


async def _campaign(client, headers, name="Spring Sale"):
    r = await client.post(f"{API}/campaigns", json={"name": name, "budget_cents": 5000}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- V2-57: draft audio upload really stores the file ---

@pytest.mark.asyncio
async def test_draft_audio_upload_stores_the_file(client, sponsor_login, monkeypatch, tmp_path):
    from app.services import storage_service

    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))
    headers, _, _ = sponsor_login
    camp = await _campaign(client, headers)
    d = await client.post(f"{API}/campaigns/{camp['id']}/drafts", json={"script_text": "Buy now"}, headers=headers)
    draft = d.json()

    audio = b"ID3" + b"\x01" * 2048
    t0 = time.perf_counter()
    r = await client.post(
        f"{API}/campaigns/{camp['id']}/drafts/{draft['id']}/upload-audio",
        files={"file": ("my spot.mp3", audio, "audio/mpeg")}, headers=headers,
    )
    assert time.perf_counter() - t0 < limit(1.0)
    assert r.status_code == 200, r.text
    path = r.json()["audio_file_path"]
    # The stored path holds exactly what the sponsor sent
    assert await storage_service.download_file(path) == audio
    drafts = (await client.get(f"{API}/campaigns/{camp['id']}/drafts", headers=headers)).json()
    assert drafts[0]["audio_file_path"] == path


@pytest.mark.asyncio
async def test_draft_audio_upload_failure_is_reported(client, sponsor_login, monkeypatch):
    """A failed upload must not look like success with a path that holds nothing."""
    headers, _, _ = sponsor_login
    camp = await _campaign(client, headers)
    draft = (await client.post(f"{API}/campaigns/{camp['id']}/drafts", json={}, headers=headers)).json()

    async def broken_upload(*a, **k):
        raise RuntimeError("storage down")

    monkeypatch.setattr("app.services.storage_service.upload_file", broken_upload)
    r = await client.post(
        f"{API}/campaigns/{camp['id']}/drafts/{draft['id']}/upload-audio",
        files={"file": ("a.mp3", b"ID3xx", "audio/mpeg")}, headers=headers,
    )
    assert r.status_code == 502
    drafts = (await client.get(f"{API}/campaigns/{camp['id']}/drafts", headers=headers)).json()
    assert drafts[0]["audio_file_path"] is None


# --- V2-58 (via API), V2-59: status changes ---

@pytest.mark.asyncio
async def test_campaign_status_change_emails_sponsor_and_refuses_unknown(client, sponsor_login, auth_headers, mails):
    headers, _, _ = sponsor_login
    camp = await _campaign(client, headers)

    r = await client.patch(f"{API}/campaigns/{camp['id']}/status", json={"status": "urgent"}, headers=auth_headers)
    assert r.status_code == 422, r.text

    # Sponsors cannot change status
    r = await client.patch(f"{API}/campaigns/{camp['id']}/status", json={"status": "approved"}, headers=headers)
    assert r.status_code == 403

    t0 = time.perf_counter()
    r = await client.patch(f"{API}/campaigns/{camp['id']}/status", json={"status": "in_review"}, headers=auth_headers)
    assert time.perf_counter() - t0 < limit(1.0)
    assert r.status_code == 200 and r.json()["status"] == "in_review"
    assert mails[-1][0] == "owner@acme.test"
    assert mails[-1][1] == "Campaign Update: Spring Sale"
    assert "In Review" in mails[-1][2]

    # Known gap documented by V2-59: an approved campaign is not locked against sponsor edits
    await client.patch(f"{API}/campaigns/{camp['id']}/status", json={"status": "approved"}, headers=auth_headers)
    r = await client.patch(f"{API}/campaigns/{camp['id']}", json={"budget_cents": 1}, headers=headers)
    assert r.status_code == 200 and r.json()["budget_cents"] == 1


@pytest.mark.asyncio
async def test_sponsor_sees_only_own_campaigns(client, sponsor_login, auth_headers, db_session):
    headers, _, _ = sponsor_login
    camp = await _campaign(client, headers)
    other = Sponsor(id=uuid.uuid4(), name="Other", length_seconds=30, audio_file_path="x")
    db_session.add(other)
    await db_session.commit()
    foreign = AdCampaign(sponsor_id=other.id, name="Not yours")
    db_session.add(foreign)
    await db_session.commit()

    listed = (await client.get(f"{API}/campaigns", headers=headers)).json()
    assert [c["id"] for c in listed] == [camp["id"]]
    assert (await client.get(f"{API}/campaigns/{foreign.id}", headers=headers)).status_code == 403
    assert len((await client.get(f"{API}/campaigns", headers=auth_headers)).json()) == 2


# --- V2-60: draft versions ---

@pytest.mark.asyncio
async def test_draft_versions_count_up_and_list_newest_first(client, sponsor_login):
    headers, _, user = sponsor_login
    camp = await _campaign(client, headers)
    for text in ("one", "two", "three"):
        r = await client.post(f"{API}/campaigns/{camp['id']}/drafts", json={"script_text": text}, headers=headers)
        assert r.status_code == 201
    drafts = (await client.get(f"{API}/campaigns/{camp['id']}/drafts", headers=headers)).json()
    assert [d["version"] for d in drafts] == [3, 2, 1]
    assert [d["script_text"] for d in drafts] == ["three", "two", "one"]
    assert all(d["user_email"] == user.email for d in drafts)


# --- V2-61, V2-62: comments and escaping ---

@pytest.mark.asyncio
async def test_staff_comment_emails_sponsor_sponsor_comment_emails_nobody(client, sponsor_login, auth_headers, mails):
    headers, _, _ = sponsor_login
    camp = await _campaign(client, headers)

    r = await client.post(f"{API}/campaigns/{camp['id']}/comments", json={"body": "Looks good"}, headers=headers)
    assert r.status_code == 201
    assert mails == []

    long_body = "Please shorten. " + "x" * 400
    r = await client.post(f"{API}/campaigns/{camp['id']}/comments", json={"body": long_body}, headers=auth_headers)
    assert r.status_code == 201
    to, subject, html = mails[-1]
    assert to == "owner@acme.test" and subject == "New Comment on Spring Sale"
    assert "testadmin@test.com" in html  # no display name -> email
    assert "x" * 184 in html and "x" * 186 not in html  # first 200 chars only

    comments = (await client.get(f"{API}/campaigns/{camp['id']}/comments", headers=headers)).json()
    assert [c["body"][:11] for c in comments] == ["Looks good", "Please shor"]


@pytest.mark.asyncio
async def test_emails_escape_user_text(client, sponsor_login, auth_headers, mails):
    headers, _, _ = sponsor_login
    camp = await _campaign(client, headers, name="<b>Big</b> Sale")
    evil = '<a href="https://phish.example">click</a><img src=x onerror=alert(1)>'
    await client.post(f"{API}/campaigns/{camp['id']}/comments", json={"body": evil}, headers=auth_headers)
    html = mails[-1][2]
    assert "<a href=\"https://phish.example\">" not in html
    assert "<img" not in html
    assert "&lt;img src=x" in html
    assert "<b>Big</b>" not in html and "&lt;b&gt;Big&lt;/b&gt;" in html

    # AI "send": body text is escaped, new lines become <br />
    r = await client.post(
        f"{API}/ai-emails/send",
        json={"to": "owner@acme.test", "subject": "Hi", "body": "Line 1\n<script>x</script>"},
        headers=auth_headers,
    )
    assert r.json()["success"] is True
    html = mails[-1][2]
    assert "Line 1<br />&lt;script&gt;x&lt;/script&gt;" in html
    assert "<script>" not in html


# --- V2-63: sending with Resend off / failing ---

@pytest.mark.asyncio
async def test_email_off_or_failing_never_fails_the_request(client, auth_headers, monkeypatch):
    import resend

    from app.config import settings
    from app.services import email_service

    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    assert await email_service.send_email("a@b.test", "s", "<p>x</p>") is False
    r = await client.post(f"{API}/ai-emails/send", json={"to": "a@b.test", "subject": "s", "body": "b"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"success": False, "message": "Email sending failed or Resend is not configured"}

    sent = []

    def ok(payload):
        sent.append(payload)
        return {"id": "1"}

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(settings, "RESEND_FROM_EMAIL", "noreply@example.test")
    monkeypatch.setattr(resend.Emails, "send", ok)
    assert await email_service.send_email("a@b.test", "Subj", "<p>x</p>") is True
    assert sent[0]["from"] == "noreply@example.test" and sent[0]["to"] == ["a@b.test"]

    def boom(payload):
        raise RuntimeError("resend 500")

    monkeypatch.setattr(resend.Emails, "send", boom)
    assert await email_service.send_email("a@b.test", "Subj", "<p>x</p>") is False

    # Links in emails use FRONTEND_URL
    captured = []

    async def cap(to, subject, html):
        captured.append(html)
        return True

    monkeypatch.setattr(settings, "FRONTEND_URL", "https://front.example")
    monkeypatch.setattr(email_service, "send_email", cap)
    await email_service.send_payment_confirmation("a@b.test", 1234)
    assert "https://front.example/sponsor/billing" in captured[0] and "$12.34" in captured[0]


# --- V2-65: AI draft fallback ---

@pytest.mark.asyncio
async def test_ai_draft_fallback_templates(client, auth_headers, monkeypatch):
    import anthropic

    from app.config import settings

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    out = {}
    for purpose in ("welcome", "renewal", "follow_up", "general", "custom"):
        t0 = time.perf_counter()
        r = await client.post(f"{API}/ai-emails/draft", json={"sponsor_name": "Acme", "purpose": purpose}, headers=auth_headers)
        assert time.perf_counter() - t0 < limit(1.0)
        out[purpose] = r.json()["draft_text"]
    assert "Acme" in out["welcome"] and "Welcome" in out["welcome"]
    assert "renew" in out["renewal"]
    assert "Acme" in out["follow_up"]
    assert out["custom"] == out["general"]

    # Claude error -> same template
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-fake")

    class Broken:
        def __init__(self, *a, **k):
            def create(**kw):
                raise RuntimeError("overloaded")
            self.messages = types.SimpleNamespace(create=create)

    monkeypatch.setattr(anthropic, "Anthropic", Broken)
    r = await client.post(f"{API}/ai-emails/draft", json={"sponsor_name": "Acme", "purpose": "welcome"}, headers=auth_headers)
    assert r.json()["draft_text"] == out["welcome"]

    # Sponsors cannot draft
    r = await client.post(f"{API}/ai-emails/draft", json={"sponsor_name": "Acme"})
    assert r.status_code in (401, 403)


# --- V2-66: invoices ---

@pytest.mark.asyncio
async def test_invoice_creation_and_summary(client, sponsor_login, auth_headers, mails):
    headers, sponsor, _ = sponsor_login
    r = await client.post(f"{API}/billing/invoices", json={"sponsor_id": str(sponsor.id), "amount_cents": 0}, headers=auth_headers)
    assert r.status_code == 422
    r = await client.post(f"{API}/billing/invoices", json={"sponsor_id": str(sponsor.id), "amount_cents": -500}, headers=auth_headers)
    assert r.status_code == 422

    r = await client.post(
        f"{API}/billing/invoices",
        json={"sponsor_id": str(sponsor.id), "amount_cents": 25000, "due_date": "2026-12-01"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["status"] == "sent" and inv["currency"] == "USD"
    assert mails[-1][1] == "New Invoice: $250.00" and "2026-12-01" in mails[-1][2]

    await client.post(f"{API}/billing/invoices", json={"sponsor_id": str(sponsor.id), "amount_cents": 100}, headers=auth_headers)
    assert "upon receipt" in mails[-1][2]

    s = (await client.get(f"{API}/billing/summary", headers=headers)).json()
    assert s == {"total_owed_cents": 25100, "total_paid_cents": 0, "pending_invoices": 2}
    mine = (await client.get(f"{API}/billing/invoices", headers=headers)).json()
    assert len(mine) == 2 and all(i["sponsor_name"] == "Acme" for i in mine)


@pytest.mark.xfail(strict=True, reason="BUG: nothing ever marks an invoice overdue; a sent invoice past its due date stays 'sent'")
@pytest.mark.asyncio
async def test_invoice_past_due_becomes_overdue(client, sponsor_login, auth_headers):
    _, sponsor, _ = sponsor_login
    past = (date.today() - timedelta(days=10)).isoformat()
    await client.post(f"{API}/billing/invoices", json={"sponsor_id": str(sponsor.id), "amount_cents": 100, "due_date": past}, headers=auth_headers)
    invoices = (await client.get(f"{API}/billing/invoices", headers=auth_headers)).json()
    assert invoices[0]["status"] == "overdue"


# --- V2-67, V2-68: Stripe ---

@pytest.mark.asyncio
async def test_stripe_checkout_returns_to_the_public_site(client, sponsor_login, auth_headers, db_session, monkeypatch):
    import stripe

    from app.config import settings

    headers, sponsor, _ = sponsor_login
    inv = Invoice(sponsor_id=sponsor.id, amount_cents=999, currency="USD", status=InvoiceStatus.SENT)
    db_session.add(inv)
    await db_session.commit()

    # Stripe off -> 400
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    assert (await client.post(f"{API}/billing/invoices/{inv.id}/checkout", headers=headers)).status_code == 400

    seen = {}

    def create(**kw):
        seen.update(kw)
        return types.SimpleNamespace(url="https://checkout.stripe.test/abc")

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:3000", "https://kbrlive.com"])
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://kbrlive.com")
    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    r = await client.post(f"{API}/billing/invoices/{inv.id}/checkout", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == "https://checkout.stripe.test/abc"
    assert seen["success_url"] == "https://kbrlive.com/sponsor/billing?payment=success"
    assert seen["cancel_url"] == "https://kbrlive.com/sponsor/billing?payment=cancelled"
    assert seen["payment_method_types"] == ["card"]
    assert seen["line_items"][0]["price_data"]["product_data"]["name"] == f"Invoice {str(inv.id)[:8]}"
    assert seen["line_items"][0]["price_data"]["unit_amount"] == 999


@pytest.mark.asyncio
async def test_stripe_webhook_marks_paid_once(client, sponsor_login, db_session, monkeypatch, mails):
    import stripe

    from app.config import settings

    _, sponsor, _ = sponsor_login
    inv = Invoice(sponsor_id=sponsor.id, amount_cents=4200, currency="USD", status=InvoiceStatus.SENT)
    db_session.add(inv)
    await db_session.commit()

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    assert (await client.post(f"{API}/billing/webhook", content=b"{}")).status_code == 400

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(lambda p, s, k: json.loads(p)))

    def event(kind, invoice_id):
        return json.dumps({"type": kind, "data": {"object": {"metadata": {"invoice_id": invoice_id}, "payment_intent": "pi_1"}}})

    # Other events and unknown invoices are ignored with 200
    assert (await client.post(f"{API}/billing/webhook", content=event("charge.refunded", str(inv.id)))).status_code == 200
    assert (await client.post(f"{API}/billing/webhook", content=event("checkout.session.completed", str(uuid.uuid4())))).status_code == 200
    await db_session.refresh(inv)
    assert inv.status == InvoiceStatus.SENT and mails == []

    for _ in range(2):  # Stripe delivers the same event twice
        r = await client.post(f"{API}/billing/webhook", content=event("checkout.session.completed", str(inv.id)))
        assert r.status_code == 200
    await db_session.refresh(inv)
    assert inv.status == InvoiceStatus.PAID and inv.paid_at is not None
    assert inv.stripe_payment_intent_id == "pi_1"
    assert [m[1] for m in mails] == ["Payment Confirmed: $42.00"]


# --- V2-69: sponsor portal counts ---

@pytest.mark.asyncio
async def test_sponsor_portal_counts_and_upcoming(client, sponsor_login, db_session):
    headers, sponsor, _ = sponsor_login
    station = Station(id=uuid.uuid4(), name="KBR NY", type=StationType.INTERNET, timezone="America/New_York")
    by_id = Asset(id=uuid.uuid4(), title="Acme 30", file_path="spots/a.mp3", asset_type="spot", sponsor_id=sponsor.id, duration=30)
    by_path = Asset(id=uuid.uuid4(), title="Acme legacy", file_path="spots/acme.mp3", asset_type="spot", duration=30)
    other = Asset(id=uuid.uuid4(), title="Other", file_path="spots/o.mp3", asset_type="spot", duration=30)
    db_session.add_all([station, by_id, by_path, other])
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    db_session.add_all([
        PlayLog(station_id=station.id, asset_id=by_id.id, start_utc=now - timedelta(minutes=5), source=PlaySource.MANUAL),
        PlayLog(station_id=station.id, asset_id=by_id.id, start_utc=month_start - timedelta(minutes=1), source=PlaySource.AD),
        PlayLog(station_id=station.id, asset_id=by_path.id, start_utc=now - timedelta(minutes=2), source=PlaySource.SCHEDULER),
        PlayLog(station_id=station.id, asset_id=other.id, start_utc=now, source=PlaySource.SCHEDULER),
    ])
    at = (now + timedelta(hours=2)).replace(microsecond=0)
    db_session.add_all([
        QueueEntry(station_id=station.id, asset_id=by_id.id, position=1, status="pending", preempt_at=at),
        QueueEntry(station_id=station.id, asset_id=by_path.id, position=2, status="pending"),  # no set time
    ])
    await db_session.commit()

    t0 = time.perf_counter()
    spots = {s["title"]: s for s in (await client.get(f"{API}/sponsor-portal/spots", headers=headers)).json()}
    assert time.perf_counter() - t0 < limit(0.5)
    assert set(spots) == {"Acme 30", "Acme legacy"}
    assert spots["Acme 30"]["plays_alltime"] == 2 and spots["Acme 30"]["plays_month"] == 1
    assert spots["Acme legacy"]["plays_alltime"] == 1

    hist = (await client.get(f"{API}/sponsor-portal/play-history", headers=headers)).json()
    assert hist["total"] == 3

    up = (await client.get(f"{API}/sponsor-portal/upcoming-schedule", headers=headers)).json()
    assert len(up) == 1  # the spot without a set time is not listed
    local = at.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    assert up[0]["station_name"] == "KBR NY"
    assert up[0]["time_slot"] == local.strftime("%I:%M %p").lstrip("0")
    assert up[0]["estimated_date"] == local.strftime("%Y-%m-%d")

