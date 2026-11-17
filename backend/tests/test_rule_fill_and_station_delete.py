"""The rule-based 7-day fill (stations without playlist generation), and deleting a
station that has a queue, schedules and history.

Rows: B-JOB-69, B-JOB-70, B-JOB-72, B-STATIONS-15."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.alert import Alert
from app.models.asset import Asset
from app.models.channel_stream import ChannelStream
from app.models.now_playing import NowPlaying
from app.models.play_log import PlayLog, PlaySource
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_rule import ScheduleRule
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.services.queue_replenish_service import TARGET_QUEUE_SECONDS, QueueReplenishService


async def _station(db, name=None) -> Station:
    st = Station(id=uuid.uuid4(), name=name or f"S{uuid.uuid4().hex[:5]}", timezone="UTC", is_active=True,
                 automation_config={})
    db.add(st)
    await db.commit()
    return st


def _asset(kind="music", title=None, duration=3600.0, **kw) -> Asset:
    return Asset(id=uuid.uuid4(), title=title or f"{kind} {uuid.uuid4().hex[:4]}", duration=duration,
                 asset_type=kind, file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3", **kw)


async def _order(db, station) -> list[Asset]:
    rows = (await db.execute(
        select(QueueEntry, Asset).join(Asset, Asset.id == QueueEntry.asset_id)
        .where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
    )).all()
    return [a for _, a in rows]


@pytest.mark.asyncio
async def test_no_rules_fills_a_week_of_random_music(db_session):
    """B-JOB-72: no rules -> random music fill up to the 7-day target; spots, test items and
    do-not-play songs are never drawn."""
    st = await _station(db_session)
    songs = [_asset() for _ in range(30)]
    db_session.add_all([*songs, _asset("spot", "A spot"), _asset("music", "DNP", category="do_not_play")])
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    order = await _order(db_session, st)
    assert {a.asset_type for a in order} == {"music"}
    assert "DNP" not in {a.title for a in order}
    assert sum(a.duration for a in order) >= TARGET_QUEUE_SECONDS


@pytest.mark.asyncio
async def test_empty_library_after_restart_raises_queue_empty_alert(db_session, monkeypatch):
    """B-JOB-72: a station whose queue is still empty after the restart's refill (nothing in
    the library) gets a queue_empty alert."""
    import app.db.engine as db_engine
    from app.main import _resume_playback_on_startup
    from tests.conftest import TestSessionLocal

    st = await _station(db_session, "Empty FM")
    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    await _resume_playback_on_startup()
    alerts = (await db_session.execute(
        select(Alert).where(Alert.station_id == st.id).execution_options(populate_existing=True)
    )).scalars().all()
    assert [a.alert_type.value if hasattr(a.alert_type, "value") else a.alert_type for a in alerts] == ["queue_empty"]


@pytest.mark.asyncio
async def test_dayparts_share_the_week_by_hours_owned(db_session):
    """B-JOB-69: two daypart rules splitting the day (music 0-12, shiurim 12-24) each fill the
    share of the week they own; a station's own rules replace the global ones."""
    st = await _station(db_session)
    db_session.add_all([_asset() for _ in range(40)] + [_asset("shiur") for _ in range(40)]
                       + [_asset("story") for _ in range(5)])
    db_session.add_all([
        ScheduleRule(id=uuid.uuid4(), station_id=st.id, name="Mornings", rule_type="daypart",
                     asset_type="music", hour_start=0, hour_end=12, priority=10),
        ScheduleRule(id=uuid.uuid4(), station_id=st.id, name="Afternoons", rule_type="daypart",
                     asset_type="shiur", hour_start=12, hour_end=24, priority=10),
        ScheduleRule(id=uuid.uuid4(), station_id=None, name="Global stories", rule_type="daypart",
                     asset_type="story", hour_start=0, hour_end=24, priority=99),
    ])
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    order = await _order(db_session, st)
    secs = {k: sum(a.duration for a in order if a.asset_type == k) for k in ("music", "shiur", "story")}
    assert secs["story"] == 0
    half = TARGET_QUEUE_SECONDS / 2
    assert abs(secs["music"] - half) <= 3600 and abs(secs["shiur"] - half) <= 3600, secs


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=(
    "BUG: a rotation rule ('a jingle every N songs') puts its items after the whole 7-day fill "
    "(positions max_pos + k*N counted from the end of the fill), so they all play back to back "
    "a week later instead of between songs"))
async def test_rotation_rule_puts_an_item_every_n_songs(db_session):
    """B-JOB-69: rotation rule with songs_between=4: the jingle comes after every 4 songs."""
    st = await _station(db_session)
    db_session.add_all([_asset() for _ in range(60)] + [_asset("jingle", duration=10.0) for _ in range(3)])
    db_session.add(ScheduleRule(id=uuid.uuid4(), station_id=st.id, name="Jingles", rule_type="rotation",
                                asset_type="jingle", songs_between=4))
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    order = await _order(db_session, st)
    kinds = [a.asset_type for a in order[:20]]
    assert "jingle" in kinds[:6], kinds


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=(
    "BUG: interval rules ('every N minutes', offered on the Rules page) are never applied: "
    "replenish runs daypart, fixed_time and rotation rules, and _apply_interval_rule is never called"))
async def test_interval_rule_queues_its_item(db_session):
    """B-JOB-69: interval rule every 30 minutes: its item is queued."""
    st = await _station(db_session)
    db_session.add_all([_asset() for _ in range(60)] + [_asset("jingle", duration=10.0) for _ in range(3)])
    db_session.add(ScheduleRule(id=uuid.uuid4(), station_id=st.id, name="Half-hourly", rule_type="interval",
                                asset_type="jingle", interval_minutes=30))
    await db_session.commit()
    await QueueReplenishService(db_session, st.id).replenish()
    assert any(a.asset_type == "jingle" for a in await _order(db_session, st))


@pytest.mark.asyncio
async def test_global_sponsors_only_for_stations_on_global_rules(db_session):
    """B-JOB-70: legacy sponsors are inserted for a station that uses the global rules, never
    for a station that has its own rules."""
    on_global = await _station(db_session)
    own = await _station(db_session)
    sponsor = Sponsor(id=uuid.uuid4(), name="Shop", length_seconds=30, audio_file_path="spots/shop.mp3",
                      insertion_policy="every_n_songs",
                      target_rules={"hour_start": 0, "hour_end": 24, "max_per_hour": 4, "songs_between": 3})
    db_session.add(sponsor)
    await db_session.flush()
    spot = Asset(id=uuid.uuid4(), title="Shop spot", duration=30.0, asset_type="spot",
                 file_path="spots/shop.mp3", sponsor_id=sponsor.id)
    db_session.add_all([spot] + [_asset() for _ in range(30)])
    db_session.add_all([
        ScheduleRule(id=uuid.uuid4(), station_id=None, name="Global music", rule_type="daypart",
                     asset_type="music", hour_start=0, hour_end=24),
        ScheduleRule(id=uuid.uuid4(), station_id=own.id, name="Own music", rule_type="daypart",
                     asset_type="music", hour_start=0, hour_end=24),
    ])
    await db_session.commit()
    for st in (on_global, own):
        await QueueReplenishService(db_session, st.id).replenish()
    assert any(a.id == spot.id for a in await _order(db_session, on_global))
    assert not any(a.id == spot.id for a in await _order(db_session, own))


@pytest.mark.asyncio
async def test_deleting_a_station_removes_its_rows_and_the_scheduler_carries_on(client, auth_headers, db_session):
    """B-STATIONS-15: deleting a station with a queue (one item on air), schedules, a now-playing
    row, play history and a channel removes them all; the scheduler's next pass over the
    remaining stations raises no error and no "Station check failed" alert."""
    from app.services.scheduler_engine import SchedulerEngine

    gone = await _station(db_session, "Gone FM")
    stays = await _station(db_session, "Stays FM")
    song = _asset(duration=200.0)
    db_session.add(song)
    await db_session.flush()
    now = datetime.now(UTC)
    for st in (gone, stays):
        db_session.add_all([
            QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=1, status="playing",
                       started_at=now - timedelta(seconds=20)),
            QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=2, status="pending"),
            PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, start_utc=now, source=PlaySource.SCHEDULER),
        ])
    db_session.add_all([
        Schedule(id=uuid.uuid4(), station_id=gone.id, name="Week"),
        NowPlaying(id=uuid.uuid4(), station_id=gone.id, asset_id=song.id, started_at=now),
        ChannelStream(id=uuid.uuid4(), station_id=gone.id, channel_name="hi", bitrate=128, codec="aac"),
    ])
    await db_session.commit()

    r = await client.delete(f"/api/v1/stations/{gone.id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    for model in (QueueEntry, PlayLog, Schedule, NowPlaying, ChannelStream):
        left = (await db_session.execute(
            select(func.count()).select_from(model).where(model.station_id == gone.id)
        )).scalar()
        assert left == 0, model.__name__
    assert (await client.get(f"/api/v1/stations/{gone.id}")).status_code == 404

    engine = SchedulerEngine()
    await engine._check_all_stations(db_session)
    for h in engine._advance_timers.values():
        h.cancel()
    failed = (await db_session.execute(select(Alert).where(Alert.title.like("Station check failed%")))).scalars().all()
    assert failed == []
    assert (await db_session.execute(
        select(func.count()).select_from(QueueEntry).where(QueueEntry.station_id == stays.id)
    )).scalar() == 2
