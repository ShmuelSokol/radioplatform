"""Placeholders never air from any automatic path, and short timed items are not crossfaded.

Live, 15:00 ET 2026-11-16: "KOL BRAMAH TEST SPONSOR", "Kol bramah sponsor" and
"Kol bramah ad" aired on KBR Jerusalem; on Main the 5 s time check handed over
after 2.5 s and the shiur it had paused started over it.
"""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


@pytest.mark.asyncio
async def test_placeholders_are_skipped_unless_an_operator_chose_them(db_session):
    from app.models.station import Station

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    test_sponsor = Asset(id=uuid.uuid4(), title="KOL BRAMAH TEST SPONSOR", duration=30.0, file_path="a", asset_type="spot")
    ad = Asset(id=uuid.uuid4(), title="Kol bramah ad ", duration=30.0, file_path="b", asset_type="spot")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="s", asset_type="music")
    db_session.add_all([station, test_sponsor, ad, song])
    await db_session.flush()
    now = datetime.now(UTC)
    rows = [
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=test_sponsor.id, position=1,
                   status="pending", source="ad_slot", preempt_at=now - timedelta(seconds=5)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=ad.id, position=2, status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=ad.id, position=3, status="pending", source=None),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=4, status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=ad.id, position=5, status="pending", source="manual"),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    ids = [r.id for r in rows]

    picked = await queue_module._pick_next_pending(db_session, station.id, now)
    assert picked is not None and picked.id == ids[3]          # the song, not a placeholder
    await db_session.commit()
    status = {r.id: r.status for r in (await db_session.execute(
        select(QueueEntry).execution_options(populate_existing=True))).scalars()}
    assert [status[i] for i in ids] == ["skipped", "skipped", "skipped", "pending", "pending"]


def test_a_short_time_check_plays_to_its_end():
    from app.api.v1.queue import switch_time

    started = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    clip = SimpleNamespace(duration=4.99, metadata_extra={})
    entry = SimpleNamespace(asset=clip, started_at=started, preempt_at=started, source=None)
    assert switch_time(entry) >= started + timedelta(seconds=4.6)

    # a 40 s weather spot with no analysis: also no 3 s crossfade
    weather = SimpleNamespace(duration=40.0, metadata_extra={})
    entry = SimpleNamespace(asset=weather, started_at=started, preempt_at=started, source=None)
    assert switch_time(entry) >= started + timedelta(seconds=39.6)

    # a song still crossfades 3 s before its end
    song = SimpleNamespace(duration=200.0, metadata_extra={})
    entry = SimpleNamespace(asset=song, started_at=started, preempt_at=None, source="auto")
    assert switch_time(entry) == started + timedelta(seconds=197.0)
