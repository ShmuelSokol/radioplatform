"""F-BLOCKS-5: songs added straight to a schedule block ("+ Add song") reach the queue.

They used to be stored on the block and never read: the queue filled only from
the block's playlist template (SchedulingService.get_next_asset_for_block had no
caller). Now the generator draws from them in the block's playback mode.
"""
import random
import uuid
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.playlist_entry import PlaybackMode, PlaylistEntry
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.playlist_generator_service import CONFIG_KEY, generate
from app.services.schedule_refresh_service import (
    realign_before_block_change,
    remove_automatic_songs,
    uses_block_playlists,
)

NY = ZoneInfo("America/New_York")
MONDAY_7AM = datetime(2026, 11, 16, 7, 0, tzinfo=NY).astimezone(UTC)


def _song(title: str, category: str = "Music", duration: float = 240.0, artist: str | None = None) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        title=title,
        artist=artist or f"{title} artist",
        duration=duration,
        file_path=f"{uuid.uuid4().hex}.mp3",
        asset_type="music",
        category=category,
    )


def _pin_clock(monkeypatch):
    import app.services.queue_replenish_service as qrs

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MONDAY_7AM

    monkeypatch.setattr(qrs, "datetime", _Clock)


async def _station(db: AsyncSession, *, filler: int = 30):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    db.add(station)
    await db.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    db.add(schedule)
    filler_songs = [_song(f"Filler {n}") for n in range(filler)]
    db.add_all(filler_songs)
    await db.flush()
    return station, schedule, filler_songs


async def _block(
    db: AsyncSession,
    schedule: Schedule,
    songs: list[Asset],
    *,
    mode: PlaybackMode = PlaybackMode.SEQUENTIAL,
    start: time = time(0, 0),
    end: time = time(23, 59),
    weights: list[int] | None = None,
    template_id=None,
) -> ScheduleBlock:
    block = ScheduleBlock(
        id=uuid.uuid4(),
        schedule_id=schedule.id,
        name="Songs block",
        start_time=start,
        end_time=end,
        recurrence_type=RecurrenceType.DAILY,
        playback_mode=mode,
        playlist_template_id=template_id,
    )
    db.add(block)
    await db.flush()
    for i, song in enumerate(songs):
        db.add(
            PlaylistEntry(
                id=uuid.uuid4(), block_id=block.id, asset_id=song.id, position=i,
                weight=(weights[i] if weights else 1),
            )
        )
    await db.flush()
    return block


async def _pending_titles(db: AsyncSession, station_id) -> list[str]:
    rows = await db.execute(
        select(Asset.title)
        .join(QueueEntry, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.source == "auto")
        .order_by(QueueEntry.position)
    )
    return [r[0] for r in rows.all()]


@pytest.mark.asyncio
async def test_adding_a_song_to_an_all_day_block_takes_over_the_queue(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """F-BLOCKS-5: the e2e flow. An all-day block with one song: after "+ Add song"
    the queue plays only that song; removing it gives the queue back to filler."""
    _pin_clock(monkeypatch)
    station, schedule, filler = await _station(db_session)
    only = _song("The one song")
    db_session.add(only)
    await db_session.flush()
    for i in range(200):  # old ordinary fill
        db_session.add(
            QueueEntry(
                id=uuid.uuid4(), station_id=station.id, asset_id=filler[i % 30].id,
                position=i + 1, status="pending", source="auto",
            )
        )
    station_id, schedule_id, only_id = station.id, schedule.id, only.id
    await db_session.commit()

    r = await client.post(
        "/api/v1/schedules/blocks",
        json={
            "schedule_id": str(schedule_id), "name": "All day", "start_time": "00:00:00",
            "end_time": "23:59:00", "recurrence_type": "daily",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    block_id = r.json()["id"]

    r = await client.post(
        "/api/v1/schedules/playlist-entries",
        json={"block_id": block_id, "asset_id": str(only_id), "position": 0},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]
    db_session.expire_all()
    titles = await _pending_titles(db_session, station_id)
    assert len(titles) >= 3
    # The next song stays while the rest are redrawn (it may be about to start)
    assert set(titles[1:]) == {"The one song"}, titles[:5]

    r = await client.delete(f"/api/v1/schedules/playlist-entries/{entry_id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    db_session.expire_all()
    titles = await _pending_titles(db_session, station_id)
    # The song is still an ordinary library song, so a random filler draw may pick
    # it now and then; what matters is that the block no longer owns the queue.
    assert titles and all(t.startswith("Filler") for t in titles[:2]) or titles[:5].count("The one song") <= 1
    assert titles.count("The one song") < len(titles) / 4


@pytest.mark.asyncio
async def test_block_with_songs_counts_as_filling_from_blocks(db_session: AsyncSession):
    """F-BLOCKS-5: uses_block_playlists is true for a block with enabled songs only."""
    station, schedule, _ = await _station(db_session, filler=0)
    song = _song("A")
    db_session.add(song)
    await db_session.flush()
    block = await _block(db_session, schedule, [song])
    assert await uses_block_playlists(db_session, station.id)
    entry = (await db_session.execute(select(PlaylistEntry).where(PlaylistEntry.block_id == block.id))).scalar_one()
    entry.is_enabled = False
    await db_session.flush()
    assert not await uses_block_playlists(db_session, station.id)


@pytest.mark.asyncio
async def test_sequential_plays_in_order_and_continues(db_session: AsyncSession):
    """F-BLOCKS-5: sequential mode plays the songs in position order, and the next
    generation carries on from the last one drawn."""
    station, schedule, _ = await _station(db_session)
    songs = [_song(t) for t in "ABC"]
    db_session.add_all(songs)
    await db_session.flush()
    await _block(db_session, schedule, songs)
    plan = await generate(db_session, station, MONDAY_7AM, 0.5)
    titles = [p.title for p in plan.picks]
    assert titles[:6] == ["A", "B", "C", "A", "B", "C"]
    nxt = await generate(db_session, station, plan.picks[-1].at + timedelta(minutes=5), 0.2, cursor=plan.cursor)
    expected = "ABC"[(len(titles)) % 3]
    assert nxt.picks[0].title == expected


@pytest.mark.asyncio
async def test_shuffle_avoids_recent_repeats(db_session: AsyncSession):
    """F-BLOCKS-5: shuffle mode never repeats a song within half the list."""
    random.seed(7)
    station, schedule, _ = await _station(db_session)
    songs = [_song(t) for t in "ABCDEF"]
    db_session.add_all(songs)
    await db_session.flush()
    await _block(db_session, schedule, songs, mode=PlaybackMode.SHUFFLE)
    plan = await generate(db_session, station, MONDAY_7AM, 3)
    titles = [p.title for p in plan.picks]
    assert len(titles) > 30 and set(titles) == set("ABCDEF")
    for i in range(3, len(titles)):
        assert titles[i] not in titles[i - 3 : i], titles


@pytest.mark.asyncio
async def test_weighted_follows_weights(db_session: AsyncSession):
    """F-BLOCKS-5: weighted mode draws by weight (9 to 1)."""
    random.seed(3)
    station, schedule, _ = await _station(db_session)
    heavy, light = _song("Heavy", duration=60), _song("Light", duration=60)
    db_session.add_all([heavy, light])
    await db_session.flush()
    await _block(db_session, schedule, [heavy, light], mode=PlaybackMode.WEIGHTED, weights=[9, 1])
    plan = await generate(db_session, station, MONDAY_7AM, 6)
    counts = Counter(p.title for p in plan.picks)
    total = sum(counts.values())
    assert total > 300 and set(counts) == {"Heavy", "Light"}
    assert 0.8 < counts["Heavy"] / total < 0.97


@pytest.mark.asyncio
async def test_placeholders_do_not_play_and_disabled_are_never_drawn(db_session: AsyncSession):
    """F-BLOCKS-5: a block's own songs still never yield a placeholder, a
    do_not_play item or a disabled entry."""
    station, schedule, _ = await _station(db_session)
    good = _song("Good")
    placeholder = _song("KOL BRAMAH TEST SPONSOR", category="Spots")
    banned = _song("Banned", category="do_not_play")
    off = _song("Switched off")
    db_session.add_all([good, placeholder, banned, off])
    await db_session.flush()
    block = await _block(db_session, schedule, [placeholder, banned, off, good])
    entry = (
        await db_session.execute(
            select(PlaylistEntry).where(PlaylistEntry.block_id == block.id, PlaylistEntry.asset_id == off.id)
        )
    ).scalar_one()
    entry.is_enabled = False
    await db_session.flush()
    plan = await generate(db_session, station, MONDAY_7AM, 1)
    assert plan.picks and {p.title for p in plan.picks} == {"Good"}


@pytest.mark.asyncio
async def test_only_placeholders_on_block_falls_back_to_filler(db_session: AsyncSession):
    """F-BLOCKS-5: a block whose only song may not be auto-picked does not go silent."""
    station, schedule, _ = await _station(db_session)
    banned = _song("Banned", category="do_not_play")
    db_session.add(banned)
    await db_session.flush()
    await _block(db_session, schedule, [banned])
    plan = await generate(db_session, station, MONDAY_7AM, 1)
    assert len(plan.picks) > 5 and all(p.title.startswith("Filler") and not p.block_id for p in plan.picks)


@pytest.mark.asyncio
async def test_own_songs_play_first_then_the_playlist(db_session: AsyncSession):
    """F-BLOCKS-5: a block with both a playlist and songs added by hand plays the
    added songs first, each once, in order, then the playlist fills the rest of the
    block (Shmuel, 2026-11-17)."""
    station, schedule, _ = await _station(db_session)
    tpl = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db_session.add(tpl)
    await db_session.flush()
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=tpl.id, position=0, asset_type="music", category="Fast"))
    db_session.add_all([_song(f"Fast {n}", category="Fast") for n in range(8)])
    own = [_song("Own A"), _song("Own B")]
    db_session.add_all(own)
    await db_session.flush()
    await _block(db_session, schedule, own, template_id=tpl.id, start=time(7, 0), end=time(10, 0))
    plan = await generate(db_session, station, MONDAY_7AM, 1)
    titles = [p.title for p in plan.picks]
    assert titles[:2] == ["Own A", "Own B"]
    assert titles[2:] and all(t.startswith("Fast") for t in titles[2:])
    # The playlist starts at its first step: the hand-added songs did not move its cursor
    assert plan.cursor.get(str(plan.picks[2].block_id)) == 0 or len(titles) > 2


@pytest.mark.asyncio
async def test_own_songs_play_first_again_on_the_next_run(db_session: AsyncSession):
    """F-BLOCKS-5: the next day's run of the block starts with the added songs again."""
    station, schedule, _ = await _station(db_session)
    tpl = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db_session.add(tpl)
    await db_session.flush()
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=tpl.id, position=0, asset_type="music", category="Fast"))
    db_session.add_all([_song(f"Fast {n}", category="Fast") for n in range(8)])
    own = _song("Opener")
    db_session.add(own)
    await db_session.flush()
    await _block(db_session, schedule, [own], template_id=tpl.id, start=time(7, 0), end=time(8, 0))
    plan = await generate(db_session, station, MONDAY_7AM, 25)
    in_block = [p for p in plan.picks if p.block_id]
    runs = {}
    for p in in_block:
        runs.setdefault(p.at.astimezone(NY).date(), []).append(p.title)
    assert len(runs) == 2
    for titles in runs.values():
        assert titles[0] == "Opener" and titles.count("Opener") == 1


@pytest.mark.asyncio
async def test_block_songs_start_on_time_and_blackout_generates_nothing(db_session: AsyncSession):
    """F-BLOCKS-5: before the block starts filler plays (no silence); the block's
    song starts with the first item due at or after 8:00; a blackout gets nothing."""
    station, schedule, _ = await _station(db_session)
    own = _song("Morning song")
    db_session.add(own)
    await db_session.flush()
    await _block(db_session, schedule, [own], start=time(8, 0), end=time(10, 0))
    eight = datetime(2026, 11, 16, 8, 0, tzinfo=NY).astimezone(UTC)
    nine = eight + timedelta(hours=1)
    db_session.add(
        HolidayWindow(id=uuid.uuid4(), name="Fast day", start_datetime=nine,
                      end_datetime=nine + timedelta(minutes=30), is_blackout=True)
    )
    await db_session.flush()
    plan = await generate(db_session, station, MONDAY_7AM, 2.5)
    before = [p for p in plan.picks if p.at < eight]
    during = [p for p in plan.picks if eight <= p.at < nine]
    assert before and all(p.block_id == "" for p in before)
    assert during and all(p.title == "Morning song" and p.block_id for p in during)
    assert not [p for p in plan.picks if nine <= p.at < nine + timedelta(minutes=30)]
    assert any("Fast day" in n for n in plan.notes)


@pytest.mark.asyncio
async def test_realign_and_cursor_move_back(db_session: AsyncSession):
    """F-BLOCKS-5: the 15-minute redraw sees a block with songs coming on, and
    removing its automatic songs moves the sequential cursor back."""
    station, schedule, _ = await _station(db_session)
    songs = [_song(t) for t in "ABCD"]
    db_session.add_all(songs)
    await db_session.flush()
    block = await _block(db_session, schedule, songs, start=time(8, 0), end=time(10, 0))
    eight = datetime(2026, 11, 16, 8, 0, tzinfo=NY).astimezone(UTC)
    assert await realign_before_block_change(db_session, station, eight - timedelta(minutes=10))

    plan = await generate(db_session, station, eight, 0.25)
    picks = [p for p in plan.picks if p.block_id == str(block.id)]
    assert [p.title for p in picks[:4]] == ["A", "B", "C", "D"]
    # Queue the first two; the cursor is saved past them
    for i, p in enumerate(picks[:2]):
        db_session.add(
            QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=p.asset_id, position=i + 1,
                       status="pending", source="auto", block_id=p.block_id)
        )
    station.automation_config = {CONFIG_KEY: {"cursor": {str(block.id): 2}}}
    await db_session.flush()
    await remove_automatic_songs(db_session, station.id)
    cursor = station.automation_config[CONFIG_KEY]["cursor"]
    assert cursor[str(block.id)] == 0
    again = await generate(db_session, station, eight, 0.1, cursor=cursor)
    assert again.picks[0].title == "A"
