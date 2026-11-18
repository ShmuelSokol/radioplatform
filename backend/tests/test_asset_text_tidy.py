"""Titles, artists and albums carry no stray tabs or spaces, however they are saved."""
import uuid

import pytest

from app.models.asset import Asset, tidy_text

API = "/api/v1/assets"


def test_tidy_text():
    assert tidy_text("\tOldies Medley (Avraham fried) ") == "Oldies Medley (Avraham fried)"
    assert tidy_text("\t") is None
    assert tidy_text("\t", keep_empty=True) == ""
    assert tidy_text("Two\twords\r\nhere") == "Two words here"
    assert tidy_text("kept  as  typed") == "kept  as  typed"   # inner spaces are the person's own
    assert tidy_text(None) is None


@pytest.mark.asyncio
async def test_a_new_item_is_tidied_whatever_wrote_it(db_session):
    a = Asset(id=uuid.uuid4(), title="\tOldies Medley ", artist="\t", album=" Best of\t", file_path="assets/x.mp3")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    assert (a.title, a.artist, a.album) == ("Oldies Medley", None, "Best of")


@pytest.mark.asyncio
async def test_saving_from_the_library_page_trims(client, auth_headers, db_session):
    a = Asset(id=uuid.uuid4(), title="Before", file_path="assets/y.mp3")
    db_session.add(a)
    await db_session.commit()
    r = await client.patch(f"{API}/{a.id}", json={"title": "  After\t", "artist": "\tAvraham Fried  "}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "After" and r.json()["artist"] == "Avraham Fried"
