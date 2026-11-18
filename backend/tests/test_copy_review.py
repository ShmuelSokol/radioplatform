import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole
from app.services import copy_review_service as svc

GOOD = (
    "Kol Berama sponsor Burger Bar. Burger Bar held its grand opening two weeks ago "
    "and offers catering for birthdays and events. The phone number for more "
    "information is 5 5 5 1 2 3 4."
)
BAD = "Come in to Burger Bar today! Buy one, get one free. Call 5551234 now."


async def _sponsor_headers(client: AsyncClient, db: AsyncSession) -> dict:
    user = User(
        id=uuid.uuid4(),
        email="sp@test.com",
        hashed_password=hash_password("pw123456"),
        role=UserRole.SPONSOR,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    login = await client.post(
        "/api/v1/auth/login", json={"email": "sp@test.com", "password": "pw123456"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_estimate_seconds_counts_digits_as_words():
    assert svc.estimate_seconds("one two three four five") == 2.0
    assert svc.estimate_seconds("call 555") == round(4 / 2.5, 1)


@pytest.mark.asyncio
async def test_rules_default_then_editable_with_history(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    r = await client.get("/api/v1/sponsorship/rules", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["version"] == 0 and r.json()["is_default"] is True
    assert "No calls to action" in r.json()["rules_text"]

    new_text = "Rule one: always say Kol Berama sponsor. Rule two: no prices."
    r = await client.put(
        "/api/v1/sponsorship/rules", json={"rules_text": new_text}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["version"] == 1
    r = await client.put(
        "/api/v1/sponsorship/rules",
        json={"rules_text": new_text + " Rule three."},
        headers=auth_headers,
    )
    assert r.json()["version"] == 2

    r = await client.get("/api/v1/sponsorship/rules", headers=auth_headers)
    assert r.json()["version"] == 2 and r.json()["rules_text"].endswith("Rule three.")
    r = await client.get("/api/v1/sponsorship/rules/history", headers=auth_headers)
    # Newest first; the built-in starting rules close the list so they can be brought back
    assert [h["version"] for h in r.json()] == [2, 1, 0]
    assert r.json()[-1]["is_default"] is True and len(r.json()[-1]["rules_text"]) > 100


@pytest.mark.asyncio
async def test_sponsor_can_read_rules_but_not_change_them(
    client: AsyncClient, db_session: AsyncSession
):
    headers = await _sponsor_headers(client, db_session)
    assert (await client.get("/api/v1/sponsorship/rules", headers=headers)).status_code == 200
    r = await client.put(
        "/api/v1/sponsorship/rules", json={"rules_text": "x" * 30}, headers=headers
    )
    assert r.status_code == 403
    assert (await client.get("/api/v1/sponsorship/rules")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_basic_check_flags_calls_to_action_and_offers(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "")
    headers = await _sponsor_headers(client, db_session)

    r = await client.post(
        "/api/v1/sponsorship/copy-review", json={"script_text": BAD}, headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "needs_changes" and body["reviewed_by"] == "basic_check"
    quotes = " | ".join(i["quote"].lower() for i in body["issues"])
    assert "come in" in quotes and "buy one, get one" in quotes and "call" in quotes
    assert any("sponsor" in i["problem"] for i in body["issues"])

    r = await client.post(
        "/api/v1/sponsorship/copy-review", json={"script_text": GOOD}, headers=headers
    )
    body = r.json()
    assert body["verdict"] == "ok" and body["issues"] == []
    assert 5 < body["estimated_seconds"] < 30 and body["length_ok"] is True


@pytest.mark.asyncio
async def test_too_long_is_flagged(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "")
    headers = await _sponsor_headers(client, db_session)
    long_copy = "Kol Berama sponsor Burger Bar. " + "They offer catering for events. " * 40
    r = await client.post(
        "/api/v1/sponsorship/copy-review", json={"script_text": long_copy}, headers=headers
    )
    assert r.json()["length_ok"] is False and r.json()["verdict"] == "needs_changes"


@pytest.mark.asyncio
async def test_ai_review_uses_the_rules_in_force(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    seen = {}

    async def fake_ai(script_text, sponsor_name, rules_text, seconds):
        seen.update(rules=rules_text, sponsor=sponsor_name)
        return {
            "verdict": "needs_changes",
            "summary": "One change needed.",
            "issues": [
                {"quote": "Call", "problem": "Call to action.", "suggestion": "Give the number."}
            ],
            "suggested_script": GOOD,
            "reviewed_by": "ai",
        }

    monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_ai_review", fake_ai)

    edited = "Only rule: the word pickle may never be said on air."
    await client.put("/api/v1/sponsorship/rules", json={"rules_text": edited}, headers=auth_headers)

    r = await client.post(
        "/api/v1/sponsorship/copy-review",
        json={"script_text": BAD, "sponsor_name": "Burger Bar"},
        headers=auth_headers,
    )
    body = r.json()
    assert body["reviewed_by"] == "ai" and body["rules_version"] == 1
    assert body["suggested_script"] == GOOD
    assert seen == {"rules": edited, "sponsor": "Burger Bar"}


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_basic_check(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    async def boom(*args):
        raise RuntimeError("api down")

    monkeypatch.setattr(svc.settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(svc, "_ai_review", boom)
    r = await client.post(
        "/api/v1/sponsorship/copy-review", json={"script_text": BAD}, headers=auth_headers
    )
    assert r.status_code == 200 and r.json()["reviewed_by"] == "basic_check"
