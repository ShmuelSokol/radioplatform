"""Automatic fill never draws test items.

A listening-test spot (category "test") was picked by a "spots every 3 songs"
rotation into KBR Jerusalem's live queue. Random picks skip test items, whatever
the case of the category; a rule that names the category still gets them.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.asset import Asset, fit_for_random_pick


@pytest.mark.asyncio
async def test_random_pick_skips_test_items(db_session):
    keep = Asset(id=uuid.uuid4(), title="Real spot", asset_type="spot", category=None,
                 duration=30.0, file_path="https://cdn.example.com/a.mp3")
    keep2 = Asset(id=uuid.uuid4(), title="Sponsor", asset_type="spot", category="sponsors",
                  duration=30.0, file_path="https://cdn.example.com/b.mp3")
    skipped = [
        Asset(id=uuid.uuid4(), title=f"ZZ Listening Test {c}", asset_type="spot", category=c,
              duration=30.0, file_path=f"https://cdn.example.com/{i}.mp3")
        for i, c in enumerate(["test", "Test", "do_not_play", "weather_spot", "Carlebach"])
    ]
    db_session.add_all([keep, keep2, *skipped])
    await db_session.commit()
    ids = set((await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "spot", fit_for_random_pick())
    )).scalars().all())
    assert ids == {keep.id, keep2.id}
