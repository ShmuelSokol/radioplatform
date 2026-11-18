"""The staff chat of a live show: host and screener write to each other off the air."""
import uuid

import pytest

from app.api.v1 import live_shows as live_shows_api
from tests.test_live_caller_desk import _show_with_callers
from tests.test_live_delay import _admin_headers


@pytest.mark.asyncio
async def test_a_line_is_stored_sent_to_the_screens_and_listed_in_order(client, db_session, monkeypatch):
    _s, show, _calls = await _show_with_callers(db_session, [])
    headers = await _admin_headers(db_session)
    said = []

    async def say(show_id, event, data):
        said.append((str(show_id), event, data))

    monkeypatch.setattr(live_shows_api, "_say", say)
    r = await client.post(f"/api/v1/live-shows/{show.id}/chat", json={"text": "  Caller 2 is   ready \n"}, headers=headers)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["text"] == "Caller 2 is ready" and first["by"] and first["at"]
    assert said == [(str(show.id), "chat", first)]
    await client.post(f"/api/v1/live-shows/{show.id}/chat", json={"text": "Wrap up in 1 minute"}, headers=headers)
    listed = (await client.get(f"/api/v1/live-shows/{show.id}/chat", headers=headers)).json()["messages"]
    assert [m["text"] for m in listed] == ["Caller 2 is ready", "Wrap up in 1 minute"]


@pytest.mark.asyncio
async def test_chat_refuses_empty_long_unknown_show_and_no_sign_in(client, db_session):
    _s, show, _calls = await _show_with_callers(db_session, [])
    headers = await _admin_headers(db_session)
    url = f"/api/v1/live-shows/{show.id}/chat"
    assert (await client.post(url, json={"text": "   "}, headers=headers)).status_code == 422
    r = await client.post(url, json={"text": "x" * 501}, headers=headers)
    assert r.status_code == 422 and "500 characters" in r.text
    assert (await client.post(url, json={"text": "x" * 500}, headers=headers)).status_code == 200
    assert (await client.post(f"/api/v1/live-shows/{uuid.uuid4()}/chat", json={"text": "hi"}, headers=headers)).status_code == 404
    assert (await client.get(f"/api/v1/live-shows/{uuid.uuid4()}/chat", headers=headers)).status_code == 404
    assert (await client.post(url, json={"text": "hi"})).status_code in (401, 403)
    assert (await client.get(url)).status_code in (401, 403)
