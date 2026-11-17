"""Show archives, podcast RSS and the RDS line (checklist V2-81..V2-84)."""
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.show_archive import ShowArchive
from app.models.station import Station, StationType
from app.models.user import User, UserRole

API = "/api/v1"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


async def _station(db_session, name="KBR Archive"):
    st = Station(id=uuid.uuid4(), name=name, type=StationType.INTERNET, timezone="America/New_York")
    db_session.add(st)
    await db_session.commit()
    return st


async def _archive(db_session, station, title="Show", published=True, **kw):
    a = ShowArchive(
        id=uuid.uuid4(), station_id=station.id, title=title, audio_url=kw.pop("audio_url", "https://cdn.test/a.mp3"),
        is_published=published, **kw,
    )
    db_session.add(a)
    await db_session.commit()
    return a


@pytest.mark.asyncio
async def test_admin_list_keeps_drafts_public_list_does_not(client, auth_headers, db_session):
    """V2-81: unpublishing hides an archive from the public, not from the admin list,
    so it can be published again from the screen."""
    st = await _station(db_session)
    pub = await _archive(db_session, st, "Published")
    draft = await _archive(db_session, st, "Draft", published=False)

    public = (await client.get(f"{API}/archives")).json()
    assert [a["title"] for a in public["archives"]] == ["Published"] and public["total"] == 1

    t0 = time.perf_counter()
    r = await client.get(f"{API}/archives?drafts=true", headers=auth_headers)
    assert time.perf_counter() - t0 < 0.5
    admin = r.json()
    assert {a["title"] for a in admin["archives"]} == {"Published", "Draft"} and admin["total"] == 2

    # Publish the draft again, then unpublish the other: both stay on the admin list
    r = await client.patch(f"{API}/archives/{draft.id}", json={"is_published": True}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["is_published"] is True
    await client.patch(f"{API}/archives/{pub.id}", json={"is_published": False}, headers=auth_headers)
    admin = (await client.get(f"{API}/archives?drafts=true", headers=auth_headers)).json()
    assert admin["total"] == 2
    # the public page never shows drafts, even to a signed-in manager
    assert (await client.get(f"{API}/archives", headers=auth_headers)).json()["total"] == 1
    assert [a["title"] for a in (await client.get(f"{API}/archives")).json()["archives"]] == ["Draft"]

    # Station filter applies to the admin list too
    other = await _station(db_session, "Other")
    await _archive(db_session, other, "Elsewhere", published=False)
    admin = (await client.get(
        f"{API}/archives", params={"station_id": str(st.id), "drafts": "true"}, headers=auth_headers
    )).json()
    assert admin["total"] == 2


@pytest.mark.asyncio
async def test_drafts_are_not_for_the_public(client, auth_headers, db_session):
    """V2-81/82: anonymous or sponsor callers cannot list or open a draft."""
    st = await _station(db_session)
    draft = await _archive(db_session, st, "Secret draft", published=False, audio_url="https://cdn.test/secret.mp3")
    pub = await _archive(db_session, st, "Open")

    assert [a["title"] for a in (await client.get(f"{API}/archives")).json()["archives"]] == ["Open"]
    r = await client.get(f"{API}/archives/{draft.id}")
    assert r.status_code == 404 and "secret.mp3" not in r.text
    assert (await client.get(f"{API}/archives/{pub.id}")).status_code == 200

    sponsor = User(id=uuid.uuid4(), email="s@sp.test", hashed_password=hash_password("sponsorpw1"), role=UserRole.SPONSOR, is_active=True)
    db_session.add(sponsor)
    await db_session.commit()
    tok = (await client.post(f"{API}/auth/login", json={"email": "s@sp.test", "password": "sponsorpw1"})).json()["access_token"]
    sp = {"Authorization": f"Bearer {tok}"}
    assert [a["title"] for a in (await client.get(f"{API}/archives", headers=sp)).json()["archives"]] == ["Open"]
    assert (await client.get(f"{API}/archives/{draft.id}", headers=sp)).status_code == 404

    r = await client.get(f"{API}/archives/{draft.id}", headers=auth_headers)
    assert r.status_code == 200 and r.json()["title"] == "Secret draft"


@pytest.mark.asyncio
async def test_podcast_rss(client, db_session):
    """V2-83: valid XML, only published items newest first (max 100), escaped text,
    RFC 822 dates, hh:mm:ss durations; a non-UUID station id is a 422, not a 500."""
    st = await _station(db_session, "KBR & Friends")
    base = datetime(2026, 11, 1, 18, 30, tzinfo=UTC)
    await _archive(db_session, st, "Old <show>", recorded_at=base - timedelta(days=1), duration_seconds=3725, host_name="R' Cohen & Co")
    await _archive(db_session, st, "New show", recorded_at=base, audio_url='https://cdn.test/a "quoted".mp3')
    await _archive(db_session, st, "Hidden", published=False)

    t0 = time.perf_counter()
    r = await client.get(f"{API}/archives/station/{st.id}/rss")
    assert time.perf_counter() - t0 < 0.5
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(r.content)  # must parse even with &, <, and a quote in a URL
    ch = root.find("channel")
    assert ch.findtext("title") == "KBR & Friends"
    items = ch.findall("item")
    assert [i.findtext("title") for i in items] == ["New show", "Old <show>"]
    assert items[0].find("enclosure").get("url") == 'https://cdn.test/a "quoted".mp3'
    assert items[0].findtext("pubDate") == "Sun, 01 Nov 2026 18:30:00 +0000"
    assert items[1].findtext(f"{ITUNES}duration") == "01:02:05"
    assert items[1].findtext(f"{ITUNES}author") == "R' Cohen & Co"

    assert (await client.get(f"{API}/archives/station/not-a-uuid/rss")).status_code == 422
    assert (await client.get(f"{API}/archives/station/{uuid.uuid4()}/rss")).status_code == 404


@pytest.mark.asyncio
async def test_podcast_rss_caps_at_100(client, db_session):
    st = await _station(db_session)
    db_session.add_all([
        ShowArchive(station_id=st.id, title=f"Ep {i}", audio_url="https://cdn.test/x.mp3", is_published=True,
                    recorded_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i))
        for i in range(105)
    ])
    await db_session.commit()
    root = ET.fromstring((await client.get(f"{API}/archives/station/{st.id}/rss")).content)
    items = root.find("channel").findall("item")
    assert len(items) == 100 and items[0].findtext("title") == "Ep 104"


@pytest.mark.xfail(strict=True, reason="BUG: podcast feed lacks channel <link>, atom:self and enclosure length, which Apple/validators require")
@pytest.mark.asyncio
async def test_podcast_rss_has_fields_directories_require(client, db_session):
    st = await _station(db_session)
    await _archive(db_session, st, "Ep", duration_seconds=60)
    root = ET.fromstring((await client.get(f"{API}/archives/station/{st.id}/rss")).content)
    ch = root.find("channel")
    assert ch.findtext("link")
    assert ch.find("{http://www.w3.org/2005/Atom}link") is not None
    assert ch.find("item").find("enclosure").get("length")


@pytest.mark.asyncio
async def test_rds_line_rules(client, db_session):
    """V2-84: nothing playing or a silence item -> station name; cut to max; no-store."""
    st = await _station(db_session, "KBR Radio Station")
    r = await client.get(f"{API}/stations/{st.id}/rds.txt")
    assert r.text == "KBR Radio Station" and r.headers["cache-control"] == "no-store"

    silence = Asset(id=uuid.uuid4(), title="Silence", file_path="s.mp3", asset_type="silence", duration=60)
    song = Asset(id=uuid.uuid4(), title="A Very Long Song Title Indeed", artist="Some Singer", file_path="x.mp3", asset_type="music", duration=200)
    db_session.add_all([silence, song])
    entry = QueueEntry(station_id=st.id, asset_id=silence.id, position=0, status="playing", started_at=datetime.now(UTC))
    db_session.add(entry)
    await db_session.commit()
    assert (await client.get(f"{API}/stations/{st.id}/rds.txt")).text == "KBR Radio Station"

    entry.asset_id = song.id
    await db_session.commit()
    assert (await client.get(f"{API}/stations/{st.id}/rds.txt")).text == "Some Singer - A Very Long Song Title Indeed"
    assert (await client.get(f"{API}/stations/{st.id}/rds.txt", params={"max": 8})).text == "Some Sin"
    assert (await client.get(f"{API}/stations/{st.id}/rds.txt", params={"max": 7})).status_code == 422
    assert (await client.get(f"{API}/stations/{st.id}/rds.txt", params={"max": 129})).status_code == 422
