import enum
import re
import uuid

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from datetime import date

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.hebrew_text import fix_garbled_hebrew

asset_categories = Table(
    "asset_categories",
    Base.metadata,
    Column("asset_id", UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


def tidy_text(value, keep_empty: bool = False):
    """Tabs, line breaks and runs of spaces become one space, the ends are trimmed; an empty artist or
    album becomes None (a title stays text: the column needs one). Hebrew that arrives
    garbled as accented Latin letters is put right (app/core/hebrew_text.py)."""
    if not isinstance(value, str):
        return value
    out = fix_garbled_hebrew(re.sub(r"[\s\u00a0]+", " ", value).strip())  # runs of spaces too, like scripts/sql/tidy_asset_text.sql
    return out if out or keep_empty else None


class AssetType(str, enum.Enum):
    MUSIC = "music"
    SPOT = "spot"
    SHIUR = "shiur"
    JINGLE = "jingle"
    ZMANIM = "zmanim"


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assets"

    @validates("title", "artist", "album")
    def _tidy_text(self, key, value):
        """No stray tabs or spaces around a name, however it arrives (form, upload tags,
        import): the library had titles like "\tOldies Medley" and an artist that was
        only a tab, which sort first and look empty (2026-11-18)."""
        return tidy_text(value, keep_empty=(key == "title"))

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    album: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Length of the intro (before the vocal), in seconds from where the item starts on air
    # (its cue-in point), not from the start of the file. Set by an operator.
    intro_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    album_art_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    asset_type: Mapped[str] = mapped_column(String(50), default="music", nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(50), default="pending", nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Staff rating, 0 (unrated) upward. Playlist generation can ask for a minimum.
    vote: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sponsors.id", ondelete="SET NULL"), nullable=True
    )

    created_by_user = relationship("User", back_populates="assets")
    categories = relationship("Category", secondary=asset_categories, back_populates="assets", lazy="selectin")
    sponsor = relationship("Sponsor", lazy="noload")


# Clips made for one moment (a dated weather report, an hour's time check). They air
# only through the timed weather/time paths, never as random filler of their type.
ONE_MOMENT_CATEGORIES = ("weather_spot", "time_announcement", "omer_count")


# Items that must never be drawn at random: marked do-not-play, or made for tests
# (a listening-test spot uploaded as music once landed in KBR Jerusalem's live queue).
# A rule or block that names one of these categories still gets it.
# "carlebach": Shmuel, 2026-11-16: his songs are kept in the library but off the air for now.
# "block_only": recordings that air only where a program block names them (the day's
# Daf Yomi shiur, the schedule announcement), never as filler.
# "kids": Shmuel, 2026-11-17: kids' songs (Uncle Moishy, Country Yossi...) stay out of general music draws.
# "kids_car_summer" / "kids_car_year": the kids-in-car safety reminders (2026-11-18). They
# air only through their seasonal program blocks; drawn as a spot, a summer heat warning
# could air in January.
NEVER_RANDOM_CATEGORIES = (
    "do_not_play", "test", "tests", "testing", "carlebach", "block_only", "kids",
    "kids_car_summer", "kids_car_year",
    # The shofar (or whatever airs at candle lighting) and other Erev Shabbos items:
    # only the station's Shabbos ending plays them (services/shabbos_ending.py)
    "shabbos_ending",
)


# Song mixes a song can belong to that play only where a playlist step names them
# (Shmuel 2026-11-19: "Eliyahu Hanavi songs only on the Havdalah mix"). A song whose
# main category is one of these never comes up in an "any song" step; a song that is
# only also in one (extra categories, `asset_categories`) still does.
MIX_ONLY_CATEGORIES = ("shabbos", "motzei_shabbos")


# Placeholder items that must never be drawn by any automatic pick, even by a
# rotation that names their category (the old "KOL BRAMAH TEST SPONSOR" sits in
# category "Spots" and kept airing from "spots every 3 songs"). Shmuel confirmed
# "Kol bramah sponsor" and "Kol bramah ad" are placeholders too. Lower case.
NEVER_AUTO_TITLES = (
    "kol bramah test sponsor",
    "kol bramah sponsor",
    "kol bramah ad",
    "kol b'ramah sponsor",
    "kol b'ramah ad",
)


# Categories no automatic pick may use, even a rule, playlist or block that names
# them: do_not_play means off the air (missing or broken files, 2026-11-17). Only an
# operator's own pick plays them. Lower case; the check ignores case and spaces.
NEVER_AUTO_CATEGORIES = ("do_not_play",)


def not_placeholder():
    """SQL condition: the asset is neither a placeholder, nor marked do-not-play, nor
    rejected in a review queue, so an automatic pick may use it (operators can still
    queue it themselves)."""
    from sqlalchemy import and_, func, or_

    return and_(
        or_(Asset.review_status.is_(None), Asset.review_status != "rejected"),
        or_(
            Asset.title.is_(None),
            func.lower(func.trim(Asset.title)).notin_(NEVER_AUTO_TITLES),
        ),
        or_(
            Asset.category.is_(None),
            func.lower(func.trim(Asset.category)).notin_(NEVER_AUTO_CATEGORIES),
        ),
    )


# Songs requested 3+ times in 30 days carry this tag (services/requested_category_service.py).
# A string, not true: Postgres' ->> gives 'true' for a JSON true but SQLite gives 1.
REQUESTED_CATEGORY = "requested"
REQUESTED_TAG = "requested"
REQUESTED_TAG_VALUE = "yes"


def in_category(category):
    """SQL condition for rotation picks: the asset is in `category`. The "requested"
    category also takes every song tagged as requested, which keeps its own category
    (and its place in that rotation) as well."""
    from sqlalchemy import or_

    from sqlalchemy import and_, func, select

    from app.models.category import Category

    condition = Asset.category == category
    if category:
        # A song can be in several categories: its main one (`category`) and extra ones
        # (`asset_categories`, Library > Also in). An item whose main category keeps it
        # off the air (do not play, tests, Carlebach, block only...) stays off.
        extra = select(asset_categories.c.asset_id).join(
            Category, Category.id == asset_categories.c.category_id
        ).where(func.lower(Category.name) == category.lower())
        condition = or_(condition, and_(
            Asset.id.in_(extra),
            or_(Asset.category.is_(None), func.lower(Asset.category).notin_(NEVER_RANDOM_CATEGORIES)),
        ))
    if category == REQUESTED_CATEGORY:
        condition = or_(condition, Asset.metadata_extra[REQUESTED_TAG].astext == REQUESTED_TAG_VALUE)
    return condition


def not_contract_audio():
    """SQL condition: not sponsorship audio. Sponsorship audio airs only through its
    contract (the spot planner) or an operator's own pick; a rotation or block that
    draws "a spot" at random would air it outside its dates, days and caps (2026-11-17).

    Sponsorship audio is any asset a spot schedule uses, and any asset tied to a
    sponsor (`sponsor_id`, set when a schedule first uses it or a recording is
    approved). The tie stays when a contract moves to new audio: on 2026-11-18 the
    30 stand-in recordings, swapped out of their contracts for the real voice, were
    drawn as filler minutes later on all three stations and two aired.
    """
    from sqlalchemy import and_, func, select
    from sqlalchemy.orm import aliased

    from app.models.spot_schedule import SpotSchedule

    # A loose copy of a contract's audio (same title, not tied to the sponsor) is
    # sponsorship audio too: Main's old "Sponsor Ad Every 30min" rule drew such copies
    # (Chessed Home Health Aides 7:31 PM beside its block play at 7:48 PM, 2026-11-18,
    # and spots contracted only for another station)
    contract = aliased(Asset)
    contract_titles = (
        select(func.lower(contract.title))
        .join(SpotSchedule, SpotSchedule.asset_id == contract.id)
        .where(contract.title.isnot(None))
    )
    return and_(
        Asset.id.notin_(select(SpotSchedule.asset_id)),
        Asset.sponsor_id.is_(None),
        func.lower(func.coalesce(Asset.title, "")).notin_(contract_titles),
    )


# Station ID jingles ("station_id", "station_id_jerusalem", ...) air only through the
# station's own "Station IDs between songs" rule or a pick that names their category.
# A playlist step, rule or block item asking for "any jingle" drew them on the first
# switch-on (2026-11-19): Jerusalem's ID on Main and on All Day Stories, IDs back to
# back and right after a sponsorship, even with the setting off.
STATION_ID_CATEGORY_PREFIX = "station_id"
# No automatic station ID shorter than this. It was 4 s after the old engine, which had
# not loaded the next song behind a 1.8 s ID, played its emergency file (Jerusalem,
# 2026-11-19); the new radio.liq fetches ahead during any item under 12 s and holds
# quiet instead. The floor keeps a broken or nearly empty file off the air.
MIN_STATION_ID_SECONDS = 1.5


def not_station_id():
    """SQL condition: not a station ID jingle (category station_id or station_id_*)."""
    from sqlalchemy import func, or_

    return or_(
        Asset.category.is_(None),
        func.lower(Asset.category).notlike(f"{STATION_ID_CATEGORY_PREFIX}%"),
    )


def fit_for_random_pick():
    """SQL condition: an asset that may be drawn at random (not do-not-play, not a
    test item, not a one-moment clip, not a placeholder). Category case does not matter."""
    from sqlalchemy import and_, func, or_

    return and_(
        or_(
            Asset.category.is_(None),
            func.lower(Asset.category).notin_((*NEVER_RANDOM_CATEGORIES, *ONE_MOMENT_CATEGORIES, *MIX_ONLY_CATEGORIES)),
        ),
        not_placeholder(),
        not_contract_audio(),
        not_station_id(),
        not_holiday_only(),
    )


def not_holiday_only():
    """SQL condition: the item is not on a holiday list as holiday-only (a Chanukah
    song, the Pesach 10 plagues series). Those air only through the holiday mix
    before and during their holiday (services/holiday_music.py), never as ordinary
    filler or from a rotation that names their category."""
    from sqlalchemy import select

    from app.models.holiday_music import HolidayItem

    return Asset.id.notin_(select(HolidayItem.asset_id).where(HolidayItem.all_year.is_(False)))


# Analyses made before this version measured every item as 180 s long (the length
# probe failed on piped audio), so their end, crossfade point and length are wrong.
ANALYSIS_VERSION = 2


def stored_analysis(metadata_extra) -> dict:
    """An item's audio analysis as far as it can be trusted. From an analysis older
    than ANALYSIS_VERSION only the start cue and loudness are kept; the end and
    crossfade points fall back to the defaults (3 s before the end)."""
    analysis = (metadata_extra or {}).get("audio_analysis") if isinstance(metadata_extra, dict) else None
    if not isinstance(analysis, dict):
        return {}
    if not _cues_make_sense(analysis):
        # A start cue at or past the end: the analysis of a silent (or nearly silent)
        # file, "silence from 0 to the end". Handed over, the item ended the moment it
        # started (the blackout's "Silence (5 min)", cue_in 300.017 of 300.01 s: the
        # emergency music on Shabbos 2026-11-20). The file plays whole instead.
        analysis = {k: v for k, v in analysis.items()
                    if k not in ("cue_in_seconds", "cue_out_seconds", "cross_start_seconds")}
    if analysis.get("version", 1) < ANALYSIS_VERSION:
        analysis = {k: analysis[k] for k in ("cue_in_seconds", "replay_gain_db", "loudness_lufs") if k in analysis}
    # The level always follows today's rule, not the one in force when the item was
    # analysed: items stored under the old -12 dB limit (a Carlebach story at -2.5 LUFS)
    # kept playing 3.5 LU louder than the rest after the limit went to -20.
    lufs = analysis.get("loudness_lufs")
    if isinstance(lufs, (int, float)) and not isinstance(lufs, bool) and _loudness_measured(analysis):
        gain = replay_gain_for(lufs)
        if analysis.get("replay_gain_db") != gain:
            analysis = {**analysis, "replay_gain_db": gain}
    return analysis


def _cues_make_sense(analysis: dict) -> bool:
    """False when the start cue is at or past the end cue (or the item's end)."""
    def num(key):
        v = analysis.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    cue_in = num("cue_in_seconds")
    if cue_in is None or cue_in <= 0:
        return True
    cue_out = num("cue_out_seconds")
    end = cue_out if cue_out is not None and cue_out > 0 else num("duration")
    if cue_out is not None and cue_out <= 0 and end is None:
        return False  # an end cue of 0 with a start cue after it
    return end is None or cue_in < end - 0.5


def replay_gain_for(loudness_lufs: float) -> float:
    """dB that brings an item to -18 LUFS. Turning down goes to -20 dB (a story mastered
    at -3 LUFS needs -15); turning up stops at +12 dB (more lifts the noise floor)."""
    return round(max(-20.0, min(12.0, -18.0 - loudness_lufs)), 2)


def _loudness_measured(analysis: dict) -> bool:
    """False where the stored loudness is a default or a reading nobody believes."""
    if analysis.get("loudness_method") == "sampled" and (analysis.get("loudness_lufs") or 0) > -5.0:
        return False
    if analysis.get("method") == "range" and not analysis.get("loudness_method"):
        return not (analysis.get("loudness_lufs", -18.0) == -18.0 and analysis.get("replay_gain_db", 0.0) == 0.0)
    return True


def has_real_loudness(analysis) -> bool:
    """False for an analysis whose loudness was never measured: a range-only
    analysis (services/tail_analysis.py) of an item with no earlier full analysis
    stored the default -18 LUFS and 0 dB, so the item played unadjusted (Jerusalem
    18:00Z, items from -13.5 to -19.3 LUFS)."""
    if not isinstance(analysis, dict) or not analysis:
        return False
    if analysis.get("loudness_method") == "sampled" and (analysis.get("loudness_lufs") or 0) > -5.0:
        return False  # a bad reading from the sampled pieces (see tail_analysis)
    if analysis.get("method") == "range" and not analysis.get("loudness_method"):
        return not (analysis.get("loudness_lufs", -18.0) == -18.0 and analysis.get("replay_gain_db", 0.0) == 0.0)
    return "loudness_lufs" in analysis or "replay_gain_db" in analysis


def needs_analysis(asset) -> bool:
    """True when the item has no analysis, one older than ANALYSIS_VERSION, or one
    whose loudness was never measured."""
    analysis = (asset.metadata_extra or {}).get("audio_analysis") if isinstance(asset.metadata_extra, dict) else None
    return (not isinstance(analysis, dict) or analysis.get("version", 1) < ANALYSIS_VERSION
            or not has_real_loudness(analysis))
