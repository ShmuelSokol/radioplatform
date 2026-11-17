"""Song requests, the "requested" category and the weather readout template
(checklist V2-110..V2-115)."""
import time
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.asset import Asset, fit_for_random_pick, in_category
from app.models.queue_entry import QueueEntry
from app.models.song_request import SongRequest
from app.models.station import Station, StationType
from app.services.requested_category_service import refresh_requested_category
from app.services.song_request_service import fuzzy_match_asset
from app.services.weather_readout_service import render_template
from tests.timing import limit

API = "/api/v1/song-requests"


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.api.v1.song_requests import limiter

    limiter.reset()
    yield
    limiter.reset()


async def _station(db):
    st = Station(id=uuid.uuid4(), name="KBR", type=StationType.INTERNET)
    db.add(st)
    await db.commit()
    return st


def _song(title, artist=None, **kw):
    return Asset(id=uuid.uuid4(), title=title, artist=artist, file_path=f"{uuid.uuid4().hex}.mp3",
                 asset_type=kw.pop("asset_type", "music"), duration=kw.pop("duration", 200), **kw)


@pytest.mark.asyncio
async def test_fuzzy_match_rules(db_session):
    """V2-110: title 70% + artist 30%, punctuation/case ignored, 0.6 threshold; a
    request without an artist still matches on an exact title (0.7)."""
    st = await _station(db_session)
    db_session.add_all([_song("Yerushalayim", "Avraham Fried"), _song("Shabbos Kodesh", None)])
    await db_session.commit()

    asset, score = await fuzzy_match_asset(db_session, "YERUSHALAYIM!!", "avraham fried", st.id)
    assert asset.title == "Yerushalayim" and score == pytest.approx(1.0)
    asset, score = await fuzzy_match_asset(db_session, "Yerushalayim", None, st.id)
    assert asset.title == "Yerushalayim" and score == pytest.approx(0.7)
    asset, score = await fuzzy_match_asset(db_session, "Shabbos Kodesh", None, st.id)
    assert score == pytest.approx(1.0)  # both without artist: not penalised
    asset, score = await fuzzy_match_asset(db_session, "Completely different", "Nobody", st.id)
    assert asset is None and score == 0.0


@pytest.mark.asyncio
async def test_requests_never_match_songs_kept_off_air(db_session):
    """V2-112: do-not-play, test items, placeholders and rejected songs are not matched."""
    st = await _station(db_session)
    db_session.add_all([
        _song("Broken File", category="do_not_play"),
        _song("Test Tone", category="test"),
        _song("Kol Bramah Sponsor"),
        _song("Rejected Song", review_status="rejected"),
        _song("Good Song"),
    ])
    await db_session.commit()
    for title in ("Broken File", "Test Tone", "Kol Bramah Sponsor", "Rejected Song"):
        asset, _ = await fuzzy_match_asset(db_session, title, None, st.id)
        assert asset is None or asset.title == "Good Song", title


@pytest.mark.asyncio
async def test_submit_auto_approve_and_queue_position(client, db_session):
    """V2-113: auto-approved request goes right after the on-air item, the next one
    after the last request; later items shift by one; position, songs ahead and wait."""
    st = await _station(db_session)
    on_air = _song("On Air", duration=240)
    filler1 = _song("Filler One", duration=180)
    filler2 = _song("Filler Two", duration=180)
    req1 = _song("Request One", "Singer", metadata_extra={"auto_approve_requests": True})
    req2 = _song("Request Two", "Singer", metadata_extra={"auto_approve_requests": True})
    db_session.add_all([on_air, filler1, filler2, req1, req2])
    await db_session.commit()
    db_session.add_all([
        QueueEntry(station_id=st.id, asset_id=on_air.id, position=0, status="playing"),
        QueueEntry(station_id=st.id, asset_id=filler1.id, position=1, status="pending"),
        QueueEntry(station_id=st.id, asset_id=filler2.id, position=2, status="pending"),
    ])
    await db_session.commit()

    t0 = time.perf_counter()
    r = await client.post(API, json={"station_id": str(st.id), "requester_name": "Dovid",
                                     "song_title": "request one", "song_artist": "singer"})
    assert time.perf_counter() - t0 < limit(0.5)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["auto_approved"] is True and body["status"] == "queued"
    assert body["matched_asset_title"] == "Request One" and body["match_confidence"] == 1.0
    assert body["queue_position"] == 1 and body["songs_ahead"] == 1 and body["estimated_wait_minutes"] == 4.0

    r = await client.post(API, json={"station_id": str(st.id), "requester_name": "Leah", "song_title": "Request Two",
                                     "song_artist": "Singer"})
    assert r.json()["queue_position"] == 2

    rows = (await db_session.execute(
        select(QueueEntry.position, Asset.title).join(Asset, QueueEntry.asset_id == Asset.id)
        .where(QueueEntry.station_id == st.id).order_by(QueueEntry.position)
    )).all()
    assert [t for _, t in rows] == ["On Air", "Request One", "Request Two", "Filler One", "Filler Two"]
    assert [p for p, _ in rows] == [0, 1, 2, 3, 4]

    # No match: saved pending with no song
    r = await client.post(API, json={"station_id": str(st.id), "requester_name": "X", "song_title": "zzzz qqqq"})
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["asset_id"] is None


@pytest.mark.asyncio
async def test_request_for_unknown_station_is_refused(client, db_session):
    """V2-112: a station that does not exist is a 404, not a saved request."""
    db_session.add(_song("Song"))
    await db_session.commit()
    for sid in (str(uuid.uuid4()), "not-a-station"):
        r = await client.post(API, json={"station_id": sid, "requester_name": "A", "song_title": "Song"})
        assert r.status_code == 404
    assert (await db_session.execute(select(SongRequest))).first() is None


@pytest.mark.asyncio
async def test_matching_speed_with_a_large_library(db_session):
    """V2-111: matching loads the whole music library in Python; with 5,000 songs it
    must still answer within S-4 (300 ms; 1 s allowed on slow CI with SQLite)."""
    st = await _station(db_session)
    db_session.add_all([_song(f"Niggun number {i}", f"Artist {i % 97}") for i in range(5000)])
    await db_session.commit()
    t0 = time.perf_counter()
    asset, score = await fuzzy_match_asset(db_session, "Niggun number 4321", "Artist 53", st.id)
    took = time.perf_counter() - t0
    assert asset is not None and asset.title == "Niggun number 4321"
    assert took < limit(1.0), f"matching 5,000 songs took {took:.2f}s"


async def _requested_three_times(db_session, category="lively", title="Popular"):
    st = await _station(db_session)
    song = _song(title, category=category)
    db_session.add(song)
    await db_session.commit()
    reqs = [SongRequest(station_id=st.id, requester_name="r", song_title=title, asset_id=song.id) for _ in range(3)]
    db_session.add_all(reqs)
    await db_session.commit()
    return song, reqs


@pytest.mark.asyncio
async def test_requested_category_tags_and_untags(db_session):
    """V2-114 (Shmuel, 2026-11-17): 3+ requests in 30 days tag the song as requested;
    it keeps its own category; the tag goes when it drops under 3."""
    song, reqs = await _requested_three_times(db_session)
    assert await refresh_requested_category(db_session) == 1
    await db_session.refresh(song)
    assert song.category == "lively" and song.metadata_extra["requested"] == "yes"
    assert "original_category" not in song.metadata_extra

    for r in reqs[:2]:
        await db_session.delete(r)
    await db_session.commit()
    assert await refresh_requested_category(db_session) == 0
    await db_session.refresh(song)
    assert song.category == "lively" and "requested" not in song.metadata_extra


@pytest.mark.asyncio
async def test_requested_song_stays_in_its_category_rotation(db_session):
    song, _ = await _requested_three_times(db_session)
    await refresh_requested_category(db_session)
    lively = (await db_session.execute(select(Asset.id).where(in_category("lively"), fit_for_random_pick()))).scalars().all()
    assert song.id in lively


@pytest.mark.asyncio
async def test_requested_song_is_picked_for_requested_rotations(db_session):
    """A block or rule on the "requested" category draws the tagged song (and songs
    staff put in "requested" by hand), in every rotation pick."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.services.playlist_generator_service import _Library
    from app.services.program_block_service import _Resolver
    from app.services.queue_replenish_service import QueueReplenishService
    from app.services.scheduling import SchedulingService

    song, _ = await _requested_three_times(db_session)
    by_hand = _song("By hand", category="requested")
    other = _song("Plain", category="lively")
    db_session.add_all([by_hand, other])
    await db_session.commit()
    await refresh_requested_category(db_session)

    ids = set((await db_session.execute(select(Asset.id).where(in_category("requested")))).scalars().all())
    assert ids == {song.id, by_hand.id}

    want = {song.id, by_hand.id}
    st = (await db_session.execute(select(Station))).scalars().first()

    # Schedule slot (scheduling.py)
    picked = {await SchedulingService(db_session)._pick_asset_for_slot("music", "requested", None) for _ in range(20)}
    assert picked <= want and picked
    # Playlist generator pools
    lib = _Library(db_session, st.id, set())
    assert {r.id for r in await lib.pool("music", "requested")} == want
    assert song.id in {r.id for r in await lib.pool("music", "lively")}
    # Queue top-up rules
    rule = SimpleNamespace(asset_type="music", category="requested")
    service = QueueReplenishService(db_session, st.id)
    service.exclude_ids = set()
    found = await service._find_assets_for_rule(rule)
    assert {a.id for a in found} <= want and found
    # Program block rotation
    row = await _Resolver(db_session, st, dry_run=True)._rotate("music", "requested", datetime.now(UTC))
    assert row.id in want


@pytest.mark.asyncio
async def test_staff_category_edit_is_never_overwritten(db_session):
    """A category staff set while the song is requested stays when the tag goes."""
    song, reqs = await _requested_three_times(db_session)
    await refresh_requested_category(db_session)
    song.category = "relax"
    await db_session.commit()
    await refresh_requested_category(db_session)
    await db_session.refresh(song)
    assert song.category == "relax"
    for r in reqs:
        await db_session.delete(r)
    await db_session.commit()
    await refresh_requested_category(db_session)
    await db_session.refresh(song)
    assert song.category == "relax" and "requested" not in song.metadata_extra


@pytest.mark.asyncio
async def test_songs_swapped_by_the_old_version_get_their_category_back(db_session):
    """Production data from before the change: category "requested" with the own
    category saved. The next refresh heals it (and tags it if still requested)."""
    song, reqs = await _requested_three_times(db_session, category="requested")
    song.metadata_extra = {"original_category": "lively", "audio_analysis": {"cue_in_seconds": 1}}
    gone = _song("Faded", category="requested", metadata_extra={"original_category": "med_fast"})
    db_session.add(gone)
    await db_session.commit()
    await refresh_requested_category(db_session)
    await db_session.refresh(song)
    await db_session.refresh(gone)
    assert song.category == "lively"
    assert song.metadata_extra == {"requested": "yes", "audio_analysis": {"cue_in_seconds": 1}}
    assert gone.category == "med_fast" and gone.metadata_extra == {}


def test_weather_readout_template():
    """V2-115: known placeholders filled; an unknown one is left as typed."""
    weather = {
        "temp_f": 72, "description": "clear sky", "wind_speed_mph": 5, "wind_direction": "NW", "humidity": 40,
        "forecast": [{"day": "Monday", "description": "rain", "high": 60, "low": 50},
                     {"day": "Tuesday", "description": "sun", "high": 65, "low": 52}],
    }
    out = render_template(
        "{city} {temp_f} {description} {wind_direction} {wind_speed_mph} {humidity} {brand_name} "
        "{day1_name} {day1_high} {day1_low} {day1_desc} {day2_name} | {forecast_text} | {date} {day_of_week} {tempf}",
        weather, city_name="Lakewood", brand_name="Kohl Baramah", target_date=date(2026, 11, 17),
    )
    assert out.startswith("Lakewood 72 clear sky NW 5 40 Kohl Baramah Monday 60 50 rain Tuesday | ")
    assert "Monday: rain, high of 60 and low of 50. Tuesday: sun, high of 65 and low of 52." in out
    assert "November 17, 2026 Tuesday" in out
    assert out.endswith("{tempf}")  # a typo stays in the script as typed
