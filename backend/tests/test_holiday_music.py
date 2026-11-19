"""Holiday music: the calendar, the ramp, both fills and the page's API
(services/holiday_music.py, wiki/holiday-music.md)."""
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_music import HolidayItem, HolidaySetting
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.holiday_music import (
    HolidayMix,
    Season,
    Setting,
    every_n,
    holiday_days,
    holidays_for_assets,
    seasons_between,
    share_on,
)
from app.services.playlist_generator_service import generate
from app.services.queue_replenish_service import QueueReplenishService

NY = ZoneInfo("America/New_York")


# --- calendar -----------------------------------------------------------------

def test_holiday_dates_5787():
    # Chanukah 5787: first candle the evening of Dec 4, 2026; 8 days
    assert holiday_days("chanukah", 5787, False) == (date(2026, 12, 5), date(2026, 12, 12))
    # 5787 is a leap year: Purim is in Adar II; Shushan Purim too in Jerusalem
    assert holiday_days("purim", 5787, False) == (date(2027, 3, 23), date(2027, 3, 23))
    assert holiday_days("purim", 5787, True) == (date(2027, 3, 23), date(2027, 3, 24))
    # Pesach: 8 days outside Israel, 7 in Israel
    assert holiday_days("pesach", 5787, False) == (date(2027, 4, 22), date(2027, 4, 29))
    assert holiday_days("pesach", 5787, True) == (date(2027, 4, 22), date(2027, 4, 28))
    # Sukkos through Simchas Torah: 9 days, 8 in Israel
    first, last = holiday_days("sukkos", 5788, False)
    assert (last - first).days == 8
    assert (holiday_days("sukkos", 5788, True)[1] - first).days == 7
    # 5788 is not a leap year: Purim in Adar
    assert holiday_days("purim", 5788, False)[0].month in (2, 3)


@pytest.mark.parametrize("israel", [False, True])
def test_every_holiday_once_in_the_coming_year(israel):
    seasons = seasons_between(date(2026, 11, 19), date(2027, 11, 18), israel)
    keys = [s.key for s in seasons if s.first >= date(2026, 11, 19)]
    # Chanukah 5787, then Tu B'Shvat ... Shavuos, then 5788's Tishrei holidays
    assert keys[:1] == ["chanukah"]
    for k in ("tu_bishvat", "purim", "pesach", "lag_baomer", "shavuos", "rosh_hashana", "yom_kippur", "sukkos"):
        assert keys.count(k) == 1, (k, keys)


# --- ramp ---------------------------------------------------------------------

CHANUKAH = Season("chanukah", "Chanukah", date(2026, 12, 5), date(2026, 12, 12))


def test_ramp_defaults():
    s = Setting()  # 14 days, 1 in 15 rising to 1 in 4, 1 in 4 during
    assert share_on(s, CHANUKAH, date(2026, 11, 20)) == 0  # 15 days before: not yet
    assert every_n(share_on(s, CHANUKAH, date(2026, 11, 21))) == 15  # 14 days before
    assert every_n(share_on(s, CHANUKAH, date(2026, 12, 4))) == 4  # the day before
    shares = [share_on(s, CHANUKAH, date(2026, 11, 21) + timedelta(days=i)) for i in range(14)]
    assert shares == sorted(shares) and len(set(shares)) == 14  # rises every day
    assert every_n(share_on(s, CHANUKAH, date(2026, 12, 8))) == 4  # during
    assert share_on(s, CHANUKAH, date(2026, 12, 13)) == 0  # after


def test_ramp_settings():
    four_weeks = Setting(lead_days=28, start_every=30, peak_every=3)
    assert share_on(four_weeks, CHANUKAH, date(2026, 11, 6)) == 0
    assert every_n(share_on(four_weeks, CHANUKAH, date(2026, 11, 7))) == 30
    assert every_n(share_on(four_weeks, CHANUKAH, date(2026, 12, 4))) == 3
    stop_at_start = Setting(stop="start")
    assert share_on(stop_at_start, CHANUKAH, date(2026, 12, 4)) > 0
    assert share_on(stop_at_start, CHANUKAH, date(2026, 12, 5)) == 0
    assert share_on(Setting(enabled=False), CHANUKAH, date(2026, 12, 4)) == 0
    assert every_n(share_on(Setting(lead_days=1, peak_every=5), CHANUKAH, date(2026, 12, 4))) == 5


def test_mix_spreads_items_evenly():
    """1 in 4 is every fourth pick, not a coin toss."""
    from types import SimpleNamespace

    rows = [SimpleNamespace(id=uuid.uuid4(), asset_type="music") for _ in range(20)]
    mix = HolidayMix(settings={"chanukah": Setting(during_every=4)}, items={"chanukah": rows},
                     zone=NY, israel=False, credit=0.0)
    mix.seasons = [CHANUKAH]
    at = datetime(2026, 12, 7, 15, 0, tzinfo=NY)
    got = [mix.pick("music", at + timedelta(minutes=4 * i)) is not None for i in range(40)]
    assert sum(got) == 10
    assert all(got[i] for i in range(3, 40, 4))
    # a jingle or spot is never replaced, and no shiurim on the list: a shiur stays
    assert mix.pick("jingle", at) is None and mix.pick("spot", at) is None
    assert all(mix.pick("shiur", at) is None for _ in range(10))
    # the songs take turns
    used = [mix.pick("music", at + timedelta(hours=3, minutes=4 * i)) for i in range(40)]
    ids = [u[0].id for u in used if u]
    assert len(set(ids)) == len(ids) == 10


def test_a_short_list_rests_each_item_three_hours():
    """Two songs at 1 in 2: each still waits three hours before it plays again, so the
    station hears them less often than the rate rather than every eight minutes."""
    from types import SimpleNamespace

    rows = [SimpleNamespace(id=uuid.uuid4(), asset_type="music") for _ in range(2)]
    mix = HolidayMix(settings={"chanukah": Setting(during_every=2)}, items={"chanukah": rows},
                     zone=NY, israel=False, credit=0.0)
    mix.seasons = [CHANUKAH]
    at = datetime(2026, 12, 7, 10, 0, tzinfo=NY)
    got = [(i, p) for i in range(90) if (p := mix.pick("music", at + timedelta(minutes=4 * i)))]
    assert len(got) == 4  # two songs, six hours: twice each
    times = defaultdict(list)
    for i, (row, _) in got:
        times[row.id].append(i * 4)
    for minutes in times.values():
        assert all(b - a >= 180 for a, b in zip(minutes, minutes[1:]))
    # a story or shiur: once a day
    stories = [SimpleNamespace(id=uuid.uuid4(), asset_type="stories") for _ in range(2)]
    mix = HolidayMix(settings={"chanukah": Setting(during_every=2)}, items={"chanukah": stories},
                     zone=NY, israel=False, credit=0.0)
    mix.seasons = [CHANUKAH]
    picked = [p for i in range(200) if (p := mix.pick("stories", at + timedelta(minutes=20 * i)))]
    assert len(picked) == 6  # 66 hours: each story once a day
    # once a song is rested again it comes back at once, but only one: no burst
    assert all(b - a >= 2 for a, b in zip([i for i, _ in got], [i for i, _ in got][1:]))


# --- the playlist fill (Main, Jerusalem) ------------------------------------------

def _asset(title, asset_type="music", category="lively", artist=None, duration=240.0):
    return Asset(id=uuid.uuid4(), title=title, artist=artist or title, duration=duration,
                 file_path=f"{uuid.uuid4().hex}.mp3", asset_type=asset_type, category=category)


async def _playlist_station(db, tz="America/New_York"):
    station = Station(id=uuid.uuid4(), name="Main", timezone=tz, is_active=True, automation_config={})
    db.add(station)
    await db.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    template = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db.add_all([schedule, template])
    await db.flush()
    for i, (t, c) in enumerate([("music", "lively"), ("music", "relax"), ("jingle", None), ("shiur", None)]):
        db.add(TemplateSlot(id=uuid.uuid4(), template_id=template.id, position=i, asset_type=t, category=c))
    db.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=schedule.id, name="All day", start_time=time(0),
                         end_time=time(23, 59), recurrence_type=RecurrenceType.DAILY,
                         playlist_template_id=template.id))
    ordinary = [_asset(f"Lively {i}") for i in range(30)] + [_asset(f"Relax {i}", category="relax") for i in range(30)]
    ordinary += [_asset(f"Jingle {i}", "jingle", None, duration=8) for i in range(5)]
    ordinary += [_asset(f"Shiur {i}", "shiur", None, duration=1800) for i in range(10)]
    # Chanukah songs filed in the ordinary categories (as the library has them)
    chanukah = [_asset(f"Maoz Tzur {i}", category="lively") for i in range(20)]
    chanukah_shiur = _asset("Chanukah shiur", "shiur", None, duration=1800)
    db.add_all(ordinary + chanukah + [chanukah_shiur])
    await db.flush()
    for a in chanukah + [chanukah_shiur]:
        db.add(HolidayItem(id=uuid.uuid4(), holiday="chanukah", asset_id=a.id))
    await db.commit()
    return station, {a.id for a in chanukah}, chanukah_shiur.id


@pytest.mark.asyncio
async def test_playlist_fill_mixes_in_holiday_items_by_the_ramp(db_session: AsyncSession):
    station, songs, shiur = await _playlist_station(db_session)
    holiday = songs | {shiur}

    async def holiday_share(day: datetime, hours=8):
        plan = await generate(db_session, station, day.astimezone(UTC), hours, artist_distance=0)
        music_or_long = [p for p in plan.picks if p.asset_type in ("music", "shiur")]
        n = sum(1 for p in plan.picks if p.asset_id in holiday)
        # never a jingle replaced; each holiday pick keeps its kind
        assert all(p.asset_type in ("music", "shiur") for p in plan.picks if p.asset_id in holiday)
        return n, len(music_or_long), plan

    # July: none, and the listed songs are never drawn as ordinary filler either
    n, total, _ = await holiday_share(datetime(2027, 7, 12, 8, 0, tzinfo=NY))
    assert n == 0 and total > 20
    # 15 days before Chanukah: still none
    n, _, _ = await holiday_share(datetime(2026, 11, 20, 8, 0, tzinfo=NY))
    assert n == 0
    # 14 days before: about 1 in 15
    n, total, _ = await holiday_share(datetime(2026, 11, 21, 8, 0, tzinfo=NY), hours=16)
    assert 1 <= n <= total / 15 + 2
    # the day before: about 1 in 4
    n, total, plan = await holiday_share(datetime(2026, 12, 4, 6, 0, tzinfo=NY))
    assert abs(n - total / 4) <= 2, (n, total)
    assert any("Chanukah" in note for note in plan.notes)
    # a holiday shiur stands in for a shiur, a song for a song
    kinds = {p.asset_type for p in plan.picks if p.asset_id in holiday}
    assert kinds <= {"music", "shiur"}
    # during Chanukah: 1 in 4; the day after it ends: none
    n, total, _ = await holiday_share(datetime(2026, 12, 8, 8, 0, tzinfo=NY))
    assert abs(n - total / 4) <= 2
    n, _, _ = await holiday_share(datetime(2026, 12, 13, 8, 0, tzinfo=NY))
    assert n == 0


@pytest.mark.asyncio
async def test_settings_change_the_mix(db_session: AsyncSession):
    station, songs, shiur = await _playlist_station(db_session)
    # four weeks ahead, no shiurim, 1 in 2 at the start
    db_session.add(HolidaySetting(station_id=station.id, holiday="chanukah", enabled=True, lead_days=28,
                                  start_every=2, peak_every=2, during_every=2, include_shiurim=False, stop="end"))
    await db_session.commit()
    plan = await generate(db_session, station, datetime(2026, 11, 10, 8, 0, tzinfo=NY).astimezone(UTC), 8,
                          artist_distance=0)
    ids = [p.asset_id for p in plan.picks]
    assert sum(1 for i in ids if i in songs) >= 10
    assert shiur not in ids
    # switched off: none
    row = await db_session.get(HolidaySetting, (station.id, "chanukah"))
    row.enabled = False
    await db_session.commit()
    plan = await generate(db_session, station, datetime(2026, 12, 4, 8, 0, tzinfo=NY).astimezone(UTC), 8,
                          artist_distance=0)
    assert not any(p.asset_id in songs for p in plan.picks)


@pytest.mark.asyncio
async def test_all_year_items_also_play_as_ordinary_songs(db_session: AsyncSession):
    station, songs, _ = await _playlist_station(db_session)
    rows = (await db_session.execute(select(HolidayItem))).scalars().all()
    for r in rows:
        r.all_year = True
    await db_session.commit()
    plan = await generate(db_session, station, datetime(2027, 7, 12, 8, 0, tzinfo=NY).astimezone(UTC), 24,
                          artist_distance=0)
    assert any(p.asset_id in songs for p in plan.picks)


@pytest.mark.asyncio
async def test_station_scope_and_israel_calendar(db_session: AsyncSession):
    """An item listed for one station only never airs on another; Jerusalem's Pesach
    ends a day earlier than Lakewood's."""
    jerusalem, songs, _ = await _playlist_station(db_session, "Asia/Jerusalem")
    other = Station(id=uuid.uuid4(), name="Other", timezone="America/New_York", is_active=True)
    db_session.add(other)
    await db_session.flush()
    for r in (await db_session.execute(select(HolidayItem))).scalars().all():
        r.station_id = other.id
    await db_session.commit()
    plan = await generate(db_session, jerusalem, datetime(2026, 12, 4, 8, 0, tzinfo=ZoneInfo("Asia/Jerusalem")), 8,
                          artist_distance=0)
    assert not any(p.asset_id in songs for p in plan.picks)
    # Pesach: day 8 is Yom Tov abroad, an ordinary day in Israel
    s = seasons_between(date(2027, 4, 1), date(2027, 5, 1), True)
    assert [x.last for x in s if x.key == "pesach"] == [date(2027, 4, 28)]


# --- the rule fill (All Day Stories) -----------------------------------------------

@pytest.mark.asyncio
async def test_rule_fill_mixes_in_holiday_stories(db_session: AsyncSession, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Stories", timezone="America/New_York", is_active=True,
                      automation_config={})
    db_session.add(station)
    stories = [_asset(f"Story {i}", "stories", None, duration=900) for i in range(40)]
    plagues = [_asset(f"Story of the 10 plagues part {i}", "music", None, duration=1100) for i in range(4)]
    plagues += [_asset(f"10 plagues story {i}", "stories", None, duration=1100) for i in range(6)]
    db_session.add_all(stories + plagues)
    await db_session.flush()
    for a in plagues:
        db_session.add(HolidayItem(id=uuid.uuid4(), holiday="pesach", asset_id=a.id, station_id=station.id))
    await db_session.commit()
    stories_ids = {a.id for a in plagues if a.asset_type == "stories"}
    music_ids = {a.id for a in plagues if a.asset_type == "music"}

    async def fill(day: datetime):
        await db_session.execute(QueueEntry.__table__.delete())
        await db_session.commit()

        async def timeline(self, now):
            self.queue_starts, self.timed_ended = [], None
            return day.astimezone(UTC), []

        monkeypatch.setattr(QueueReplenishService, "_air_timeline", timeline)
        svc = QueueReplenishService(db_session, station.id)
        svc.exclude_ids, svc.queued_ids, svc.max_pos = set(), set(), 0
        await svc._fill_content(24 * 3600, asset_type="stories")
        rows = (await db_session.execute(select(QueueEntry.asset_id).where(QueueEntry.station_id == station.id))).scalars().all()
        return rows

    # Two months before Pesach: never, though the list's stories are of the type drawn
    rows = await fill(datetime(2027, 2, 20, 8, 0, tzinfo=NY))
    assert not set(rows) & (stories_ids | music_ids)
    # The day before Pesach: 1 in 4 would be ~24 of a day's 95 stories, but each story
    # airs at most once a day, so the six on the list each once; only stories drawn
    rows = await fill(datetime(2027, 4, 21, 8, 0, tzinfo=NY))
    got = [r for r in rows if r in stories_ids]
    assert sorted(got) == sorted(stories_ids), (len(got), len(rows))
    assert not set(rows) & music_ids


# --- queue tag and API ----------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_tag_only_in_season(db_session: AsyncSession):
    station, songs, _ = await _playlist_station(db_session)
    one = next(iter(songs))
    tags = await holidays_for_assets(db_session, station, [one], datetime(2026, 12, 1, 12, tzinfo=UTC))
    assert tags == {str(one): "Chanukah"}
    assert await holidays_for_assets(db_session, station, [one], datetime(2027, 7, 1, 12, tzinfo=UTC)) == {}


@pytest.mark.asyncio
async def test_api(client: AsyncClient, db_session: AsyncSession, auth_headers: dict, monkeypatch):
    from app.api.v1 import holiday_music as api

    redrawn = []

    async def fake_redraw(db, stations):
        redrawn.extend(str(s.id) for s in stations)
        return [str(s.id) for s in stations]

    monkeypatch.setattr(api, "_redraw", fake_redraw)
    station, songs, _ = await _playlist_station(db_session)
    sid = str(station.id)
    r = await client.get(f"/api/v1/holiday-music?station_id={sid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [h["key"] for h in body["holidays"]]
    assert sorted(keys) == sorted(["rosh_hashana", "yom_kippur", "sukkos", "chanukah", "tu_bishvat", "purim",
                                    "pesach", "lag_baomer", "shavuos"])
    chanukah = next(h for h in body["holidays"] if h["key"] == "chanukah")
    assert chanukah["items"]["music"] == 20 and chanukah["items"]["long"] == 1
    assert chanukah["settings"]["lead_days"] == 14

    r = await client.put(f"/api/v1/holiday-music/chanukah/settings?station_id={sid}",
                         json={"lead_days": 28, "start_every": 20}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["lead_days"] == 28 and r.json()["settings"]["peak_every"] == 4
    r = await client.put(f"/api/v1/holiday-music/chanukah/settings?station_id={sid}",
                         json={"lead_days": 500}, headers=auth_headers)
    assert r.status_code == 422
    r = await client.get(f"/api/v1/holiday-music/chanukah/days?station_id={sid}", headers=auth_headers)
    assert r.status_code == 200
    days = r.json()["days"]
    first = next(d for d in days if d["phase"] == "holiday")
    assert days[0]["every"] == 20 and len([d for d in days if d["phase"] == "before"]) == 28
    assert first["every"] == 4

    # suggest finds library titles; adding them takes ordinary queued copies out
    extra = _asset("Chanukah Medley")
    db_session.add(extra)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=extra.id, position=1,
                              status="pending", source="auto"))
    await db_session.commit()
    r = await client.get("/api/v1/holiday-music/chanukah/suggest", headers=auth_headers)
    assert [s["title"] for s in r.json()] == ["Chanukah Medley"]
    # a Daf Yomi shiur of Masechta Pesachim is not a Pesach item
    db_session.add_all([_asset("Pesachim 110", "shiur", None), _asset("Seder night shiur", "shiur", None)])
    await db_session.commit()
    r = await client.get("/api/v1/holiday-music/pesach/suggest", headers=auth_headers)
    assert [s["title"] for s in r.json()] == ["Seder night shiur"]
    r = await client.post("/api/v1/holiday-music/chanukah/items",
                          json={"asset_ids": [str(extra.id)]}, headers=auth_headers)
    assert {k: r.json()[k] for k in ("added", "already_listed", "removed_from_queues")} == {
        "added": 1, "already_listed": 0, "removed_from_queues": 1}
    r = await client.post("/api/v1/holiday-music/chanukah/items",
                          json={"asset_ids": [str(extra.id)]}, headers=auth_headers)
    assert r.json()["added"] == 0
    r = await client.get(f"/api/v1/holiday-music/chanukah/items?station_id={sid}", headers=auth_headers)
    items = r.json()
    assert len(items) == 22
    item = next(i for i in items if i["asset_id"] == str(extra.id))
    r = await client.patch(f"/api/v1/holiday-music/items/{item['id']}",
                           json={"station_id": sid, "all_year": True}, headers=auth_headers)
    assert r.json()["station_id"] == sid and r.json()["all_year"] is True
    r = await client.delete(f"/api/v1/holiday-music/items/{item['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert (await client.get("/api/v1/holiday-music/nope/items", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_top_ups_carry_the_count_on(db_session: AsyncSession):
    """A queue topped up one song at a time keeps the rate: the count goes on from the
    last holiday item in the queue, instead of a coin toss for every top-up."""
    station, songs, _ = await _playlist_station(db_session)
    ordinary = (await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "music", Asset.id.notin_(songs)).limit(3)
    )).scalars().all()
    at = datetime(2026, 12, 4, 10, 0, tzinfo=NY).astimezone(UTC)  # the day before: 1 in 4

    async def queue(ids):
        await db_session.execute(QueueEntry.__table__.delete())
        for i, a in enumerate(ids):
            db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a, position=i + 1,
                                      status="pending", source="auto"))
        await db_session.commit()

    for _ in range(5):
        # a holiday song and then three ordinary ones waiting: the next song is a holiday one
        await queue([next(iter(songs)), *ordinary])
        plan = await generate(db_session, station, at, 0.05, artist_distance=0)
        assert plan.picks[0].asset_id in songs
        # a holiday song last in the queue: the next is an ordinary one
        await queue([*ordinary, next(iter(songs))])
        plan = await generate(db_session, station, at, 0.05, artist_distance=0)
        assert plan.picks[0].asset_id not in songs


@pytest.mark.asyncio
async def test_a_change_in_season_redraws_the_queue(db_session: AsyncSession, monkeypatch):
    """A queue is drawn two days ahead: starting the mix earlier, or adding songs to a
    list in season, draws the waiting automatic songs again so it airs now, not in
    two days (Shmuel 2026-11-19, "start putting in the Hanukkah songs to play now").
    Out of season nothing is redrawn."""
    from app.api.v1 import holiday_music as api

    calls = []

    async def fake_replace(db, station_id):
        calls.append(str(station_id))
        return 0

    monkeypatch.setattr("app.services.schedule_refresh_service.replace_automatic_songs", fake_replace)
    monkeypatch.setattr(api, "REDRAW_INLINE", True)
    station, songs, _ = await _playlist_station(db_session)
    jerusalem = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    db_session.add(jerusalem)
    await db_session.commit()
    nov19 = date(2026, 11, 19)
    monkeypatch.setattr(api, "_today", lambda st: nov19)

    # 16 days before Chanukah: 14 days ahead reaches the queue's last days, 16 is today
    assert api.touches_queue(station, "chanukah", [Setting()])  # Nov 21 is within 4 days
    assert not api.touches_queue(station, "chanukah", [Setting(lead_days=10)])
    assert not api.touches_queue(station, "purim", [Setting(lead_days=30)])

    from app.models.user import User

    admin = User(id=uuid.uuid4(), email="a@x", hashed_password="x", role="admin")
    body = api.SettingIn(lead_days=16)
    out = await api.save_settings("chanukah", body, station.id, db_session, admin)
    assert out["queue_redrawn"] is True and calls == [str(station.id)]
    calls.clear()
    # the same setting again: nothing changed, nothing redrawn
    out = await api.save_settings("chanukah", body, station.id, db_session, admin)
    assert out["queue_redrawn"] is False and calls == []
    # Purim is months away: a change is saved, the queue left alone
    out = await api.save_settings("purim", api.SettingIn(lead_days=20), station.id, db_session, admin)
    assert out["queue_redrawn"] is False and calls == []

    # a song added for every station: each station in season is redrawn
    extra = _asset("O Hanukka")
    db_session.add(extra)
    await db_session.commit()
    out = await api.add_items("chanukah", api.ItemsIn(asset_ids=[extra.id]), db_session, admin)
    assert out["queues_redrawn"] == 2 and set(calls) == {str(station.id), str(jerusalem.id)}
    calls.clear()
    # switched off on Jerusalem: a later list change leaves Jerusalem's queue alone
    await api.save_settings("chanukah", api.SettingIn(enabled=False), jerusalem.id, db_session, admin)
    calls.clear()
    item = (await db_session.execute(select(HolidayItem).where(HolidayItem.asset_id == extra.id))).scalar_one()
    await api.remove_item(item.id, db_session, admin)
    assert calls == [str(station.id)]


@pytest.mark.asyncio
async def test_space_out_parts_two_waiting_holiday_items(db_session: AsyncSession):
    """Two holiday songs that end up next to each other in the queue (a jingle between
    does not count) are swapped apart; the item on air and the next two stay put."""
    from app.services.holiday_music import space_out

    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True)
    db_session.add(station)
    songs = [_asset(f"Song {i}") for i in range(6)]
    h1, h2, h3 = _asset("Maoz Tzur"), _asset("O Hanukka"), _asset("Al Hanisim")
    jingle = _asset("ID", "jingle", None, duration=8)
    db_session.add_all(songs + [h1, h2, h3, jingle])
    await db_session.flush()
    for h in (h1, h2, h3):
        db_session.add(HolidayItem(id=uuid.uuid4(), holiday="chanukah", asset_id=h.id))
    order = [songs[0], songs[1], songs[2], h1, jingle, h2, songs[3], songs[4], h3, songs[5]]
    for pos, a in enumerate(order, start=1):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=pos,
                                  status="playing" if pos == 1 else "pending", source="auto"))
    await db_session.commit()
    assert await space_out(db_session, station.id) == 1
    await db_session.commit()
    rows = (await db_session.execute(
        select(QueueEntry.asset_id).where(QueueEntry.station_id == station.id).order_by(QueueEntry.position)
    )).scalars().all()
    titles = {a.id: a.title for a in order}
    got = [titles[r] for r in rows]
    assert got == ["Song 0", "Song 1", "Song 2", "Maoz Tzur", "ID", "Song 3", "O Hanukka", "Song 4",
                   "Al Hanisim", "Song 5"]
    assert await space_out(db_session, station.id) == 0
