"""Hebrew tags stored as Windows-1255 but read as Latin-1 ("àìéäå äðáéà") are put right
when an item is saved, whatever wrote it; real Latin text is never touched."""
import uuid

import pytest

from app.core.hebrew_text import fix_garbled_hebrew
from app.models.asset import Asset


@pytest.mark.parametrize("garbled,right", [
    ("àìéäå äðáéà", "אליהו הנביא"),                     # on air on Main 2026-11-18 08:37Z
    ("îùôçú åàê", "משפחת ואך"),
    ("C Lanzbom (àìéäå)", "C Lanzbom (אליהו)"),          # Hebrew part in brackets
    ("Song 1 - ùìåí òìéëí", "Song 1 - שלום עליכם"),
])
def test_garbled_hebrew_is_put_right(garbled, right):
    assert fix_garbled_hebrew(garbled) == right
    # exactly what a tag reader does: Windows-1255 bytes read as Latin-1
    assert fix_garbled_hebrew(right.encode("cp1255").decode("latin-1")) == right


@pytest.mark.parametrize("text", [
    "café", "Beyoncé", "été", "Voilà à Paris", "Niño", "Motörhead", "à", "ÀÉ", "Señor",
    "אליהו הנביא", "Shalom Aleichem", "", "Dvořák", "naïve – live",
])
def test_real_text_is_left_alone(text):
    assert fix_garbled_hebrew(text) == text


def test_none_and_numbers_pass_through():
    assert fix_garbled_hebrew(None) is None
    assert fix_garbled_hebrew(5) == 5


@pytest.mark.asyncio
async def test_a_new_item_is_stored_in_proper_hebrew(db_session):
    a = Asset(id=uuid.uuid4(), title="àìéäå äðáéà", artist="îùôçú åàê", album="\tùéøéí ", file_path="x.mp3")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    assert (a.title, a.artist, a.album) == ("אליהו הנביא", "משפחת ואך", "שירים")


@pytest.mark.asyncio
async def test_bulk_create_and_edit_store_proper_hebrew(client, auth_headers, db_session):
    r = await client.post("/api/v1/assets/bulk-create", headers=auth_headers, json={"assets": [
        {"title": "àìéäå äðáéà", "artist": "îùôçú åàê", "file_path": "music/garbled.mp3"},
        {"title": "Café del Mar", "artist": "José", "file_path": "music/latin.mp3"},
    ]})
    assert r.status_code == 201, r.text
    listed = (await client.get("/api/v1/assets", headers=auth_headers, params={"limit": 50})).json()["assets"]
    by_path = {a["file_path"]: a for a in listed}
    assert by_path["music/garbled.mp3"]["title"] == "אליהו הנביא"
    assert by_path["music/garbled.mp3"]["artist"] == "משפחת ואך"
    assert (by_path["music/latin.mp3"]["title"], by_path["music/latin.mp3"]["artist"]) == ("Café del Mar", "José")
    # editing a title to garbled text from the library page is put right too
    aid = by_path["music/latin.mp3"]["id"]
    r = await client.patch(f"/api/v1/assets/{aid}", headers=auth_headers, json={"title": "ùìåí òìéëí"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "שלום עליכם"
