import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.rds import radiotext
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


def _asset(**kw) -> Asset:
    return Asset(id=uuid.uuid4(), file_path="x.mp3", **kw)


def test_music_shows_artist_and_title():
    a = _asset(title="Lo Nafsik Lirkod", artist="Ari Goldwag", asset_type="music")
    assert radiotext(a, "Kol Berama") == "Ari Goldwag - Lo Nafsik Lirkod"


def test_sponsorship_shows_the_sponsor_line_only():
    a = _asset(title="JUDAICA PLAZA SUPER STORE", artist="voice talent", asset_type="spot")
    assert radiotext(a, "Kol Berama") == "JUDAICA PLAZA SUPER STORE"


def test_jingles_and_silence_show_the_station_name():
    assert radiotext(_asset(title="wmdi-lp lakewood", asset_type="jingle"), "Kol Berama") == "Kol Berama"
    assert radiotext(None, "Kol Berama") == "Kol Berama"


def test_line_is_trimmed_and_made_rds_safe():
    a = _asset(title="A very long title — with a dash\nand a line break " + "x" * 80, artist="Someone", asset_type="music")
    line = radiotext(a, "Kol Berama")
    assert len(line) <= 64 and "\n" not in line and "—" not in line


@pytest.mark.asyncio
async def test_endpoint_is_public_plain_text(client: AsyncClient, db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Kol Berama", timezone="America/New_York")
    asset = _asset(title="B'ou", artist="Alumni", asset_type="music")
    db_session.add_all([station, asset])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=1, status="playing"))
    await db_session.commit()

    r = await client.get(f"/api/v1/stations/{station.id}/rds.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "Alumni - B'ou"
