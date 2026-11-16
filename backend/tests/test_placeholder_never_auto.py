"""The old placeholder spot never airs from an automatic pick.

"KOL BRAMAH TEST SPONSOR" sits in category "Spots", so a "spots every 3 songs"
rotation naming that category kept drawing it into KBR Jerusalem's live queue
even after test categories were skipped. No automatic pick may use it, and a
refill removes copies already queued, leaving what an operator added.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.asset import Asset, fit_for_random_pick, not_placeholder
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService


def _spot(title, category="Spots"):
    return Asset(id=uuid.uuid4(), title=title, asset_type="spot", category=category,
                 duration=30.0, file_path=f"https://cdn.example.com/{uuid.uuid4()}.mp3")


@pytest.mark.asyncio
async def test_no_pick_draws_the_placeholder(db_session):
    real = _spot("Sponsor Grocery")
    placeholder = _spot("KOL BRAMAH TEST SPONSOR")
    lower = _spot(" kol bramah test sponsor ", category=None)
    sponsor = _spot("Kol bramah sponsor")
    ad = _spot("KOL BRAMAH AD")
    db_session.add_all([real, placeholder, lower, sponsor, ad])
    await db_session.commit()

    random_ids = set((await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "spot", fit_for_random_pick())
    )).scalars().all())
    assert random_ids == {real.id}

    # A rotation that names the category still never gets it.
    named_ids = set((await db_session.execute(
        select(Asset.id).where(Asset.category == "Spots", not_placeholder())
    )).scalars().all())
    assert named_ids == {real.id}


@pytest.mark.asyncio
async def test_refill_removes_queued_placeholder_copies(db_session):
    station = Station(id=uuid.uuid4(), name="Placeholder FM", timezone="America/New_York")
    real = _spot("Sponsor Grocery")
    placeholder = _spot("KOL BRAMAH TEST SPONSOR")
    db_session.add_all([station, real, placeholder])
    await db_session.flush()
    now = datetime.now(timezone.utc)
    auto_copy = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=placeholder.id,
                           position=1, status="pending", source="auto")
    slot_copy = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=placeholder.id,
                           position=2, status="pending", source="ad_slot", preempt_at=now)
    operator_copy = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=placeholder.id,
                               position=3, status="pending", source="manual")
    real_entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=real.id,
                            position=4, status="pending", source="auto")
    db_session.add_all([auto_copy, slot_copy, operator_copy, real_entry])
    await db_session.commit()

    svc = QueueReplenishService(db_session, station.id)
    svc.automation_config = {}
    await svc._purge_placeholders(keep_ad_slots=False)
    await db_session.commit()

    left = set((await db_session.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == station.id)
    )).scalars().all())
    assert left == {operator_copy.id, real_entry.id}
