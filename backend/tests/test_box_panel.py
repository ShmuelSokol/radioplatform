"""The local FM box's own control room (infra/local/kbr_panel.py) and what it hands to
the site when the line is back (app/services/box_sync.py). Shmuel, 2026-11-19: someone
must be able to go to the BMG room and run the station with no internet."""

import importlib.util
import json
import socket
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from tests.test_local_relay import TOKEN, _ext, _next, rig  # noqa: F401  (fixture)

_spec = importlib.util.spec_from_file_location(
    "kbr_panel", Path(__file__).resolve().parents[2] / "infra" / "local" / "kbr_panel.py")
kbr_panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kbr_panel)


def _offline_box(fake, relay, clock):
    now = clock["now"]
    fake.plan = {"blackouts": [], "timezone": "America/New_York", "entries": [
        fake.entry("q1", 1), fake.entry("q2", 2), fake.entry("tc", 3, at=now + 1800, mode="hard", kind="jingle"),
    ]}
    fake.extension = _ext(fake, now)
    relay.sync_extension()
    relay.fetch_plan_audio()
    relay.library = {f"lib-{n}": {"url": fake.url(n), "kind": "music", "title": f"Song {n}", "artist": "A", "duration": 200.0}
                     for n in ("s1", "s2", "s3")}
    relay.library["lib-missing"] = {"url": fake.url("nothere"), "kind": "music", "title": "Song missing", "artist": "A",
                                    "duration": 100.0}
    fake.up = False
    relay._set_online(False)
    return kbr_panel.Panel(relay)


def test_play_next_now_and_at_a_time_offline(rig):  # noqa: F811
    fake, relay, clock = rig
    panel = _offline_box(fake, relay, clock)
    now = clock["now"]
    assert [i["title"] for i in panel.library("song")] == ["Song s1", "Song s2", "Song s3", "Song missing"]
    assert panel.library("missing")[0]["on_disk"] is False
    assert panel.play("lib-missing", "next")["ok"] is False  # not on this disk
    # Play next: before the queue's own next item
    r = panel.play("lib-s2", "next")
    assert r["ok"]
    assert [c["title"] for c in panel.coming(now)[:3]] == ["Song s2", "q1", "q2"]
    _, item = _next(relay)
    assert item["entry_id"] == r["entry_id"] and item["uri"].endswith(str(relay.local_path(fake.url("s2"))))
    # Play now: the engine's timed lane, on air within seconds
    assert panel.play("lib-s3", "now")["ok"]
    assert fake.timed[-1]["at"] == now + kbr_panel.PLAY_NOW_LEAD and 'kbr_lane="timed"' in fake.timed[-1]["uri"]
    # At a time: handed over like any timed item
    r = panel.play("lib-s1", "at", at=now + 3600, mode="hard")
    assert r["ok"] and panel.play("lib-s1", "at", at=now - 5)["ok"] is False
    clock["now"] = now + 3600 - 40
    relay.hand_over_timed()
    assert fake.timed[-1]["id"] == r["entry_id"]


def test_take_off_and_move_offline_and_what_the_site_hears(rig):  # noqa: F811
    fake, relay, clock = rig
    panel = _offline_box(fake, relay, clock)
    assert panel.remove("q1")["ok"]
    assert relay.removed_site == [{"entry_id": "q1", "title": "q1", "at": None}]
    assert panel.move("q2", "next")["ok"]
    added = panel.play("lib-s1", "end")["entry_id"]
    assert panel.remove(added)["ok"] and added not in relay.added_local  # added and taken off here: nothing to tell
    assert panel.move("tc", "next")["ok"] is False  # an item at a set time is not moved
    assert _next(relay)[1]["entry_id"] == "q2"


def test_online_the_site_is_in_charge(rig):  # noqa: F811
    fake, relay, clock = rig
    panel = kbr_panel.Panel(relay)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = kbr_panel.serve(panel, port, "127.0.0.1")
    base = f"http://127.0.0.1:{port}"

    def post(path, body, headers=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {"X-Panel": "1"})})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    try:
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "KBR box control room" in page
        state = json.loads(urllib.request.urlopen(base + "/api/state", timeout=5).read())
        assert state["online"] is True and state["can_change"] is False
        assert post("/api/play", {"id": "x", "when": "next"})[0] == 409
        assert post("/api/play", {"id": "x"}, headers={"X-Other": "1"})[0] == 403  # no X-Panel: not this page
    finally:
        server.shutdown()


def test_password_on_the_network(rig):  # noqa: F811
    fake, relay, clock = rig
    panel = kbr_panel.Panel(relay, password="pw123")
    assert not panel.signed_in(None) and not panel.signed_in("kbr_panel=wrong")
    assert not panel.login("nope") and panel.login("pw123")
    assert panel.signed_in(f"other=1; kbr_panel={panel.token()}")


def test_usb_recording_offline_then_into_the_library_and_the_log(rig, tmp_path):  # noqa: F811
    """A shiur loaded from a USB stick airs offline. When the line is back the box
    uploads it first, then its kept start report names the new library item, then
    the additions still to come go to the site."""
    fake, relay, clock = rig
    panel = _offline_box(fake, relay, clock)
    assert panel.upload("notes.txt", "shiur", "", b"x")["ok"] is False
    up = panel.upload("Rabbi Weinberger 5787.mp3", "shiur", "Rabbi Weinberger", b"ID3fakeaudio")
    assert up["ok"] and up["title"] == "Rabbi Weinberger"
    assert panel.library("weinberger")[0]["id"] == up["id"]
    eid = panel.play(up["id"], "next")["entry_id"]
    later = panel.play("lib-s1", "at", at=clock["now"] + 7200)["entry_id"]
    _, item = _next(relay)
    assert item["entry_id"] == eid and item["uri"].endswith(relay.uploads[up["id"][7:]]["path"])
    relay.started(json.dumps({"entry_id": eid, "lane": "main"}).encode())
    kept = json.loads(relay.spool_path.read_text().splitlines()[-1])
    assert kept["asset_id"] == up["id"]
    # line back: the stand-in site makes the asset and takes the changes
    uploaded = []

    class Site:
        pass

    fake.up = True
    real_urlopen = urllib.request.urlopen

    def fake_urlopen(req, timeout=None):
        if getattr(req, "full_url", "").endswith("/box-upload"):
            uploaded.append((req.headers.get("X-upload-id"), req.data))

            class R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return b'{"ok":true,"asset_id":"site-asset-1"}'
            return R()
        return real_urlopen(req, timeout=timeout)

    import pytest as _p

    mp = _p.MonkeyPatch()
    mp.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        relay.catch_up()
    finally:
        mp.undo()
    assert uploaded and uploaded[0][1] == b"ID3fakeaudio"
    started = [json.loads(b) for m, p, b in fake.calls if p == "/started"]
    assert started[-1]["asset_id"] == "site-asset-1"
    changes = [json.loads(b) for m, p, b in fake.calls if p == "/box-changes"]
    assert [a["title"] for a in changes[0]["added"]] == ["Song s1"]
    assert changes[0]["added"][0]["at"] == relay.local_items[later]["at"]
    assert relay.added_local == [] and relay.front == []


@pytest.mark.asyncio
async def test_site_takes_the_box_changes(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    import time

    from sqlalchemy import select

    from app.models.alert import Alert

    station = Station(id=uuid.uuid4(), name="FM", timezone="America/New_York")
    a = Asset(id=uuid.uuid4(), title="Shiur from USB", duration=600.0, file_path="https://cdn.example.com/u.mp3",
              asset_type="shiur")
    db_session.add_all([station, a])
    await db_session.commit()
    sid, aid = station.id, str(a.id)
    later = time.time() + 7200
    body = {"added": [{"asset_id": aid, "title": "Shiur from USB", "at": later, "mode": "hard"},
                      {"asset_id": aid, "title": "Past", "at": time.time() - 60},
                      {"asset_id": "upload:nope", "title": "Never uploaded"}],
            "removed": [{"entry_id": "x", "title": "TEST SPONSOR 3", "at": later}]}
    assert (await client.post(f"/api/v1/playout/{sid}/box-changes", json=body)).status_code == 403
    resp = await client.post(f"/api/v1/playout/{sid}/box-changes", json=body, headers={"X-Playout-Token": TOKEN})
    assert resp.status_code == 200 and resp.json()["added"] == 1
    db_session.expire_all()
    rows = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == sid))).scalars().all()
    assert [(str(r.asset_id), r.source, r.timing_mode) for r in rows] == [(aid, "manual", "hard")]
    alert = (await db_session.execute(select(Alert).where(Alert.station_id == sid))).scalars().one()
    assert "TEST SPONSOR 3" in alert.message and "Never uploaded" in alert.message
    assert str(alert.severity.value if hasattr(alert.severity, "value") else alert.severity) == "warning"


@pytest.mark.asyncio
async def test_usb_recording_becomes_one_library_item(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    from app.services import weather_spot_service

    stored = []

    async def fake_store(audio, stem):
        stored.append((audio, stem))
        return f"https://cdn.example.com/{stem}.mp3", 612.0

    monkeypatch.setattr(weather_spot_service, "_store_upload", fake_store)
    sid = uuid.uuid4()
    headers = {"X-Playout-Token": TOKEN, "X-Upload-Id": "abc123", "X-Title": "Rabbi%20Freedman%20%E2%80%93%20Mir",
               "X-Kind": "shiur", "Content-Type": "application/octet-stream"}
    r1 = await client.post(f"/api/v1/playout/{sid}/box-upload", content=b"ID3audio", headers=headers)
    r2 = await client.post(f"/api/v1/playout/{sid}/box-upload", content=b"ID3audio", headers=headers)
    assert r1.status_code == 200 and r1.json()["asset_id"] == r2.json()["asset_id"]  # sent twice: one item
    assert len(stored) == 1
    asset = await db_session.get(Asset, uuid.UUID(r1.json()["asset_id"]))
    assert asset.title == "Rabbi Freedman – Mir" and asset.asset_type == "shiur" and asset.duration == 612.0
    bad = dict(headers, **{"X-Playout-Token": "wrong"})
    assert (await client.post(f"/api/v1/playout/{sid}/box-upload", content=b"x", headers=bad)).status_code == 403
