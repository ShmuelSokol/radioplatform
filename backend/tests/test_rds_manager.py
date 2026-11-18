"""RDS manager: limits, Hebrew, the rotation, sponsor lines, the feeds and the log."""
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.rds import RdsMessage, RdsPlay
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import rds_service as rds


def _station(**kw) -> Station:
    return Station(id=uuid.uuid4(), name=kw.pop("name", f"Kol B'ramah Main {uuid.uuid4().hex[:4]}"),
                   timezone="America/New_York", **kw)


def _msg(text: str, **kw) -> RdsMessage:
    kw.setdefault("is_active", True)
    kw.setdefault("weight", 1)
    kw.setdefault("days_of_week", "0,1,2,3,4,5,6")
    kw.setdefault("kind", "text")
    return RdsMessage(id=uuid.uuid4(), text=text, **kw)


def _ctx(station=None, messages=(), on_air=None, weather=None, sponsors=None, **settings):
    s = dict(rds.DEFAULTS)
    s.update(settings)
    return rds.Context(station or _station(), s, list(messages), sponsors or {}, on_air, weather or {})


def _asset(**kw) -> Asset:
    return Asset(id=uuid.uuid4(), file_path="x.mp3", **kw)


# ── limits and characters ──────────────────────────────────────────


def test_radiotext_never_over_64_and_cuts_at_a_word():
    line = rds.fit("Kol B'ramah sponsor Lakewood Kosher Bakery, fresh challah and cakes baked daily on site")
    assert len(line) <= 64
    assert line == "Kol B'ramah sponsor Lakewood Kosher Bakery, fresh challah and"
    assert line.startswith("Kol B'ramah sponsor Lakewood Kosher Bakery")


def test_a_64_character_line_is_left_whole():
    exact = "A" * 64
    assert rds.fit(exact) == exact
    assert rds.fit(exact + "B") == exact


def test_ps_is_at_most_8_characters():
    assert rds.fit_ps("KOLBRAMAH") == "KOLBRAMA"
    assert rds.default_ps("Kol B'ramah Main") == "KOLBRAMA"
    assert rds.default_ps("WMDI") == "WMDI"
    for name in ["Kol B'ramah - All Day Stories", "KBR Jerusalem", "x"]:
        assert len(rds.default_ps(name)) <= 8


def test_hebrew_is_dropped_and_separators_tidied():
    assert rds.rds_safe("Avraham Fried - ואני תפילתי") == "Avraham Fried"
    assert rds.rds_safe("שלום") == ""
    assert rds.has_hebrew("Yiddish וואס")


def test_hebrew_only_song_shows_the_fallback_not_a_hole():
    song = _asset(title="ואני תפילתי", artist="אברהם פריד", asset_type="music")
    st = rds.compute(_ctx(on_air=song, fallback_text="Kol B'ramah 107.9 Lakewood"), datetime.now(UTC))
    assert st.rt == "Kol B'ramah 107.9 Lakewood"
    assert st.kind == "fallback"


def test_hebrew_title_with_latin_artist_shows_artist_only_never_a_dangling_dash():
    song = _asset(title="ואני תפילתי", artist="Avraham Fried", asset_type="music")
    assert rds.song_line(song) == "Avraham Fried"


def test_accents_and_typographic_punctuation_fold_to_ascii():
    assert rds.rds_safe("Café “Kol” — B’ramah…") == 'Cafe "Kol" - B\'ramah...'
    assert rds.rds_safe("line\nbreak\ttab") == "line break tab"


def test_transliteration_override_in_metadata_wins():
    song = _asset(title="ואני תפילתי", artist="אברהם פריד", asset_type="music",
                  metadata_extra={"rds_text": "Avraham Fried - V'ani Sefilasi"})
    assert rds.song_line(song) == "Avraham Fried - V'ani Sefilasi"


def test_check_text_flags_length_hebrew_and_sponsor_wording():
    long = rds.check_text("x " * 40)
    assert not long["ok"] and "64" in long["errors"][0]
    heb = rds.check_text("Shabbos שבת times")
    assert heb["ok"] and any("Hebrew" in w for w in heb["warnings"])
    assert heb["example"] == "Shabbos times"
    sponsor = rds.check_text("Call now! Best prices at Cohen's", kind="sponsor", sponsor_name="Cohen's")
    assert any("Sponsorship rules" in w for w in sponsor["warnings"])
    ok = rds.check_text("Kol B'ramah sponsor Cohen's Hardware. Info 732-555-0100", kind="sponsor")
    assert not [w for w in ok["warnings"] if "identify" in w]
    ps = rds.check_text("hi", ps_text="KOLBRAMAH")
    assert not ps["ok"]


def test_placeholder_line_too_long_only_warns_since_real_values_vary():
    r = rds.check_text("{weather} {weather} {weather}")
    assert r["ok"] and r["warnings"]


# ── rotation and schedule ──────────────────────────────────────────


def test_weighted_cycle_spreads_turns():
    assert rds.weighted_cycle([("a", 3), ("b", 1)]) == ["a", "a", "b", "a"]
    cyc = rds.weighted_cycle([("np", 2), ("m1", 1), ("m2", 1)])
    assert cyc == ["np", "m1", "m2", "np"]  # the two song turns are not back to back
    assert rds.weighted_cycle([("x", 0)]) == []


def test_rotation_walks_through_lines_each_slot():
    a, b = _msg("Kol B'ramah - Lakewood's Jewish radio"), _msg("Listen at kbrlive.com")
    song = _asset(title="B'ou", artist="Alumni", asset_type="music")
    ctx = _ctx(messages=[a, b], on_air=song, rotation_seconds=10, now_playing_weight=1)
    t0 = datetime(2026, 11, 18, 17, 0, 0, tzinfo=UTC)
    seen = [rds.compute(ctx, t0 + timedelta(seconds=10 * i)).rt for i in range(3)]
    assert sorted(seen) == sorted(["Alumni - B'ou", a.text, b.text])
    # Same slot = same answer (every poll agrees)
    assert rds.compute(ctx, t0).rt == rds.compute(ctx, t0 + timedelta(seconds=9)).rt


def test_message_window_days_and_dates():
    tz_local = datetime(2026, 11, 18, 8, 30)  # Wednesday (weekday 2), 8:30 local
    m = _msg("Morning", start_time=time(6), end_time=time(10))
    from zoneinfo import ZoneInfo
    local = tz_local.replace(tzinfo=ZoneInfo("America/New_York"))
    assert rds.runs_at(m, local)
    assert not rds.runs_at(m, local.replace(hour=11))
    m.days_of_week = "0,1"
    assert not rds.runs_at(m, local)
    night = _msg("Night", start_time=time(22), end_time=time(2), days_of_week="1")  # Tuesday night
    assert rds.runs_at(night, local.replace(hour=1))  # Wednesday 1 AM = Tuesday's run
    assert not rds.runs_at(night, local.replace(hour=23))  # Wednesday 11 PM is not Tuesday
    dated = _msg("Chanukah", start_date=date(2026, 12, 4), end_date=date(2026, 12, 12))
    assert not rds.runs_at(dated, local)
    off = _msg("Off", is_active=False)
    assert not rds.runs_at(off, local)


def test_time_and_weather_placeholders():
    st = _station()
    now = datetime(2026, 11, 18, 20, 5, tzinfo=UTC)  # 3:05 PM in Lakewood
    ctx = _ctx(station=st, messages=[_msg("The time is {time} on Kol B'ramah")], now_playing_weight=0)
    assert rds.compute(ctx, now).rt == "The time is 3:05 PM on Kol B'ramah"
    wx = _ctx(station=st, messages=[_msg("Lakewood {temp}, {weather}")], now_playing_weight=0,
              weather={"temp": "48F", "weather": "light rain"})
    assert rds.compute(wx, now).rt == "Lakewood 48F, light rain"
    # Weather down: the weather line is skipped, not shown with holes
    nowx = _ctx(station=st, messages=[_msg("Lakewood {temp}, {weather}")], now_playing_weight=0)
    assert rds.compute(nowx, now).kind == "fallback"


def test_jingle_on_air_skips_the_now_playing_turn():
    jingle = _asset(title="wmdi-lp lakewood", asset_type="jingle")
    m = _msg("Kol B'ramah 107.9 FM")
    ctx = _ctx(messages=[m], on_air=jingle, now_playing_weight=5)
    for i in range(6):
        assert rds.compute(ctx, datetime(2026, 11, 18, tzinfo=UTC) + timedelta(seconds=15 * i)).rt == m.text


def test_sponsor_line_takes_over_while_their_spot_airs():
    sponsor_id = uuid.uuid4()
    spot = _asset(title="COHEN HARDWARE 30", asset_type="spot", sponsor_id=sponsor_id)
    line = _msg("Kol B'ramah sponsor Cohen's Hardware, Route 9", kind="sponsor", sponsor_id=sponsor_id,
                start_time=time(23), end_time=time(23, 1))  # outside its window: still shows during the spot
    other = _msg("Slogan")
    ctx = _ctx(messages=[line, other], on_air=spot, sponsors={sponsor_id: "Cohen's Hardware"})
    for i in range(4):
        st = rds.compute(ctx, datetime(2026, 11, 18, 15, tzinfo=UTC) + timedelta(seconds=15 * i))
        assert st.kind == "sponsor_on_air" and st.rt == line.text and st.sponsor_id == sponsor_id
    off = _ctx(messages=[line, other], on_air=spot, sponsor_while_spot_airs=False)
    assert rds.compute(off, datetime(2026, 11, 18, 15, tzinfo=UTC)).kind != "sponsor_on_air"


def test_spot_without_rds_line_shows_its_title():
    spot = _asset(title="Kol B'ramah sponsor Judaica Plaza", asset_type="spot")
    st = rds.compute(_ctx(on_air=spot), datetime.now(UTC))
    assert st.rt == "Kol B'ramah sponsor Judaica Plaza" and st.kind == "sponsor_on_air"


def test_ps_static_by_default_and_rotates_only_when_asked():
    m = _msg("Shabbos zmanim", ps_text="ZMANIM")
    ctx = _ctx(messages=[m], now_playing_weight=0, ps_text="KBR1079")
    assert rds.compute(ctx, datetime.now(UTC)).ps == "KBR1079"
    ctx = _ctx(messages=[m], now_playing_weight=0, ps_text="KBR1079", ps_mode="rotate")
    assert rds.compute(ctx, datetime.now(UTC)).ps == "ZMANIM"


# ── HTTP ───────────────────────────────────────────────────────────


async def _login(client, db, role=UserRole.ADMIN):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"), role=role, is_active=True))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_feeds_and_play_log(client: AsyncClient, db_session: AsyncSession):
    station = _station()
    sponsor = Sponsor(id=uuid.uuid4(), name="Cohen's Hardware", length_seconds=30, audio_file_path="x")
    db_session.add_all([station, sponsor])
    await db_session.flush()
    db_session.add(_msg("Kol B'ramah sponsor Cohen's Hardware", kind="sponsor", sponsor_id=sponsor.id,
                        station_id=station.id))
    await db_session.commit()
    h = await _login(client, db_session)
    r = await client.put(f"/api/v1/rds/stations/{station.id}/settings", headers=h,
                         json={"ps_text": "KBR1079", "now_playing_weight": 0, "rotation_seconds": 15})
    assert r.status_code == 200, r.text
    rds._last_logged.clear()

    r = await client.get(f"/api/v1/stations/{station.id}/rds.txt")
    assert r.status_code == 200 and r.text == "Kol B'ramah sponsor Cohen's Hardware"
    assert r.headers["cache-control"] == "no-store"
    assert (await client.get(f"/api/v1/stations/{station.id}/rds-ps.txt")).text == "KBR1079"
    j = (await client.get(f"/api/v1/stations/{station.id}/rds.json")).json()
    assert j["ps"] == "KBR1079" and j["kind"] == "message" and j["sponsor_name"] == "Cohen's Hardware"

    plays = (await db_session.execute(select(RdsPlay).where(RdsPlay.station_id == station.id))).scalars().all()
    assert len(plays) == 1 and plays[0].sponsor_id == sponsor.id  # repeated polls fold into one row
    r = await client.get(f"/api/v1/rds/plays?station_id={station.id}", headers=h)
    assert r.json()["sponsor_totals"][0]["sponsor_name"] == "Cohen's Hardware"


@pytest.mark.asyncio
async def test_old_now_playing_mode_still_works(client: AsyncClient, db_session: AsyncSession):
    station = _station()
    song = _asset(title="B'ou", artist="Alumni", asset_type="music")
    db_session.add_all([station, song])
    await db_session.flush()
    db_session.add(_msg("Slogan line", station_id=station.id))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1, status="playing"))
    await db_session.commit()
    r = await client.get(f"/api/v1/stations/{station.id}/rds.txt?mode=now_playing")
    assert r.text == "Alumni - B'ou"


@pytest.mark.asyncio
async def test_message_crud_validation_and_roles(client: AsyncClient, db_session: AsyncSession):
    station = _station()
    db_session.add(station)
    await db_session.commit()
    admin = await _login(client, db_session)
    dj = await _login(client, db_session, UserRole.DJ)
    base = {"station_id": str(station.id), "text": "Kol B'ramah - 107.9 FM Lakewood"}

    assert (await client.post("/api/v1/rds/messages", headers=dj, json=base)).status_code == 403
    r = await client.post("/api/v1/rds/messages", headers=admin, json=base)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert (await client.get(f"/api/v1/rds/messages?station_id={station.id}", headers=dj)).status_code == 200

    too_long = await client.post("/api/v1/rds/messages", headers=admin, json={**base, "text": "y" * 70})
    assert too_long.status_code == 422
    hebrew_only = await client.post("/api/v1/rds/messages", headers=admin, json={**base, "text": "שבת שלום"})
    assert hebrew_only.status_code == 422
    no_sponsor = await client.post("/api/v1/rds/messages", headers=admin, json={**base, "kind": "sponsor"})
    assert no_sponsor.status_code == 422
    bad_ps = await client.post("/api/v1/rds/messages", headers=admin, json={**base, "ps_text": "TOOLONGPS"})
    assert bad_ps.status_code == 422
    bad_days = await client.post("/api/v1/rds/messages", headers=admin, json={**base, "days_of_week": "7"})
    assert bad_days.status_code == 422

    r = await client.patch(f"/api/v1/rds/messages/{mid}", headers=admin, json={"weight": 3, "start_time": "06:00"})
    assert r.status_code == 200 and r.json()["weight"] == 3 and r.json()["start_time"] == "06:00"
    r = await client.get(f"/api/v1/rds/stations/{station.id}/preview?minutes=2", headers=admin)
    assert r.status_code == 200 and r.json()["now"]["rt"]
    assert all(len(row["rt"]) <= 64 and len(row["ps"]) <= 8 for row in [r.json()["now"], *r.json()["coming"]])
    assert (await client.delete(f"/api/v1/rds/messages/{mid}", headers=dj)).status_code == 403
    assert (await client.delete(f"/api/v1/rds/messages/{mid}", headers=admin)).status_code == 204

    bad = await client.put(f"/api/v1/rds/stations/{station.id}/settings", headers=admin, json={"ps_text": "כול"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_one_stations_lines_never_show_on_another(client: AsyncClient, db_session: AsyncSession):
    a, b = _station(), _station()
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add(_msg("Only on A", station_id=a.id))
    db_session.add(_msg("Every station", station_id=None))
    await db_session.commit()
    h = await _login(client, db_session)
    texts_b = [m["text"] for m in (await client.get(f"/api/v1/rds/messages?station_id={b.id}", headers=h)).json()["messages"]]
    assert "Only on A" not in texts_b and "Every station" in texts_b
    await client.put(f"/api/v1/rds/stations/{a.id}/settings", headers=h, json={"ps_text": "AAAA"})
    assert (await client.get(f"/api/v1/stations/{b.id}/rds-ps.txt")).text != "AAAA"


def test_a_line_with_nothing_to_show_gives_up_its_turn_instead_of_doubling_the_next():
    slogan, wx, other = _msg("Slogan"), _msg("Weather {temp}"), _msg("Other line")
    ctx = _ctx(messages=[slogan, wx, other], now_playing_weight=2)  # no song, no weather
    t0 = datetime(2026, 11, 18, 17, 0, 0, tzinfo=UTC)
    seen = [rds.compute(ctx, t0 + timedelta(seconds=15 * i)).rt for i in range(8)]
    assert set(seen) == {"Slogan", "Other line"}
    assert all(a != b for a, b in zip(seen, seen[1:]))
