"""A listener types "One Day by Matisyahu" into the title box and leaves the
artist empty. That used to score as a title of "one day by matisyahu" with no
artist, so it never matched the song in the library."""
import uuid

import pytest

from app.models.asset import Asset
from app.services.song_request_service import fuzzy_match_asset, split_title_artist


def _song(title, artist):
    return Asset(id=uuid.uuid4(), title=title, artist=artist, asset_type="music",
                 duration=200.0, file_path=f"https://cdn.example.com/{uuid.uuid4()}.mp3")


def test_split_title_artist():
    assert split_title_artist("One day by matisyahu", None) == ("One day", "matisyahu")
    assert split_title_artist("One day by matisyahu", "") == ("One day", "matisyahu")
    # An artist typed in its own box wins; the title is left alone.
    assert split_title_artist("One day by matisyahu", "MBD") == ("One day by matisyahu", "MBD")
    assert split_title_artist("Racheim", None) == ("Racheim", None)


@pytest.mark.asyncio
async def test_title_with_by_artist_matches(db_session):
    target = _song("One Day", "Matisyahu")
    db_session.add_all([target, _song("Racheim", "Yaakov Shwekey"), _song("Avadim", "Miami Boys Choir")])
    await db_session.flush()

    asset, conf = await fuzzy_match_asset(db_session, "One day by matisyahu", None, "s")
    assert asset is not None and asset.id == target.id
    assert conf >= 0.9


@pytest.mark.asyncio
async def test_title_containing_by_still_matches_as_typed(db_session):
    target = _song("Stand By Me", None)
    db_session.add_all([target, _song("Stand", "Someone Else")])
    await db_session.flush()

    asset, _ = await fuzzy_match_asset(db_session, "Stand By Me", None, "s")
    assert asset is not None and asset.id == target.id


@pytest.mark.asyncio
async def test_unrelated_request_still_unmatched(db_session):
    db_session.add_all([_song("Racheim", "Yaakov Shwekey")])
    await db_session.flush()

    asset, conf = await fuzzy_match_asset(db_session, "One day by matisyahu", None, "s")
    assert asset is None and conf == 0.0
