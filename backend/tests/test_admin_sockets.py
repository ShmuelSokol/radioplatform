"""The sockets admin pages and every site tab keep open: /listeners/ws (refetch the
live count when told) and /ws/site (release state, open-tab list).

Rows: B-LISTENERS-5, B-LISTENERS-6, B-SITERELEAS-8, B-SITERELEAS-9."""
import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from app.api.v1 import listeners as listeners_api
from app.api.v1 import site_release


class HeldSocket:
    """A socket that hands out its scripted messages, then waits until closed."""

    def __init__(self, *messages, fail_send=False):
        self.inbox = [m if isinstance(m, str) else json.dumps(m) for m in messages]
        self.sent = []
        self.fail_send = fail_send
        self.gone = asyncio.Event()

    async def accept(self):
        pass

    async def receive_text(self):
        if self.inbox:
            return self.inbox.pop(0)
        await self.gone.wait()
        raise WebSocketDisconnect(1000)

    async def send_text(self, text):
        if self.fail_send:
            raise RuntimeError("socket is dead")
        self.sent.append(json.loads(text))


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_listener_watchers_told_to_refetch_and_dead_ones_dropped():
    """B-LISTENERS-5 / B-LISTENERS-6: an admin page's socket is told {"type": "listeners"} when
    someone joins or leaves; a socket that cannot be written to is dropped; a closed page
    leaves the watcher list."""
    good, dead = HeldSocket(), HeldSocket(fail_send=True)
    tasks = [asyncio.create_task(listeners_api.watch_listeners(s)) for s in (good, dead)]
    await _settle()
    assert {good, dead} <= listeners_api._watchers

    await listeners_api._notify_watchers()
    assert good.sent == [{"type": "listeners"}]
    assert dead not in listeners_api._watchers and good in listeners_api._watchers

    good.gone.set()
    dead.gone.set()
    await asyncio.gather(*tasks)
    assert good not in listeners_api._watchers


@pytest.mark.asyncio
async def test_listener_watcher_pinged_when_quiet(monkeypatch):
    """B-LISTENERS-5: a quiet socket gets a ping so proxies keep it open."""
    monkeypatch.setattr(listeners_api, "WATCH_PING_SECONDS", 0.01)
    s = HeldSocket()
    task = asyncio.create_task(listeners_api.watch_listeners(s))
    await asyncio.sleep(0.05)
    s.gone.set()
    await task
    assert {"type": "ping"} in s.sent


@pytest.fixture
def site_db(monkeypatch, db_session):
    import app.db.engine as db_engine
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    site_release._tabs.clear()
    yield db_session
    site_release._tabs.clear()


@pytest.mark.asyncio
async def test_site_tab_gets_release_state_and_shows_cleaned_in_tab_list(client, auth_headers, site_db):
    """B-SITERELEAS-8 / B-SITERELEAS-9: a tab is sent the release state at once; its "tab"
    message is kept with only the known keys (long text cut to 200, objects dropped) and
    listed for admins; bad JSON is ignored; closing the tab removes it."""
    hello = {"type": "tab", "data": {"client_id": "c1", "version": "v42", "page": "/listen/x" + "y" * 300,
                                     "playing": True, "user": {"id": 1}, "secret": "nope"}}
    s = HeldSocket(hello, "not json")
    task = asyncio.create_task(site_release.site_socket(s))
    await asyncio.sleep(0.05)
    assert s.sent and s.sent[0]["type"] == "release" and s.sent[0]["data"]["mode"] == "auto"

    tabs = (await client.get("/api/v1/site-release/tabs", headers=auth_headers)).json()["tabs"]
    assert len(tabs) == 1
    tab = tabs[0]
    assert tab["client_id"] == "c1" and tab["version"] == "v42" and tab["playing"] is True
    assert len(tab["page"]) == 200 and tab["user"] is None and "secret" not in tab

    s.gone.set()
    await task
    assert (await client.get("/api/v1/site-release/tabs", headers=auth_headers)).json()["tabs"] == []


@pytest.mark.asyncio
async def test_silent_site_tab_expires(client, auth_headers, site_db, monkeypatch):
    """B-SITERELEAS-9: a tab that says nothing for TAB_SILENT_LIMIT is dropped from the list,
    and its socket is closed off."""
    import time

    s = HeldSocket()
    task = asyncio.create_task(site_release.site_socket(s))
    await asyncio.sleep(0.05)
    tab = next(iter(site_release._tabs.values()))
    tab.seen_at = time.time() - site_release.TAB_SILENT_LIMIT - 1      # hidden from the list at once
    assert (await client.get("/api/v1/site-release/tabs", headers=auth_headers)).json()["tabs"] == []

    monkeypatch.setattr(site_release, "TAB_SILENT_LIMIT", 0.05)
    s2 = HeldSocket()
    task2 = asyncio.create_task(site_release.site_socket(s2))
    await asyncio.wait_for(task2, timeout=2)                            # times out on its own
    assert all(t.ws is not s2 for t in site_release._tabs.values())
    s.gone.set()
    await task
