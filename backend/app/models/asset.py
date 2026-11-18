import enum
import re
import uuid

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from datetime import date

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

asset_categories = Table(
    "asset_categories",
    Base.metadata,
    Column("asset_id", UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


def tidy_text(value, keep_empty: bool = False):
    """Tabs and line breaks become one space, the ends are trimmed; an empty artist or
    album becomes None (a title stays text: the column needs one)."""
    if not isinstance(value, str):
        return value
    out = re.sub(r"[\t\r\n\x0b\x0c\u00a0]+", " ", value).strip()
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
ONE_MOMENT_CATEGORIES = ("weather_spot", "time_announcement")


# Items that must never be drawn at random: marked do-not-play, or made for tests
# (a listening-test spot uploaded as music once landed in KBR Jerusalem's live queue).
# A rule or block that names one of these categories still gets it.
# "carlebach": Shmuel, 2026-11-16: his songs are kept in the library but off the air for now.
# "block_only": recordings that air only where a program block names them (the day's
# Daf Yomi shiur, the schedule announcement), never as filler.
# "kids": Shmuel, 2026-11-17: kids' songs (Uncle Moishy, Country Yossi...) stay out of general music draws.
NEVER_RANDOM_CATEGORIES = ("do_not_play", "test", "tests", "testing", "carlebach", "block_only", "kids")


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

    condition = Asset.category == category
    if category == REQUESTED_CATEGORY:
        condition = or_(condition, Asset.metadata_extra[REQUESTED_TAG].astext == REQUESTED_TAG_VALUE)
    return condition


def not_contract_audio():
    """SQL condition: no spot schedule uses the asset. Sponsorship audio airs only
    through its contract (the spot planner) or an operator's own pick; a rotation
    or block that draws "a spot" at random would air it outside its dates, days
    and caps (2026-11-17)."""
    from sqlalchemy import select

    from app.models.spot_schedule import SpotSchedule

    return Asset.id.notin_(select(SpotSchedule.asset_id))


def fit_for_random_pick():
    """SQL condition: an asset that may be drawn at random (not do-not-play, not a
    test item, not a one-moment clip, not a placeholder). Category case does not matter."""
    from sqlalchemy import and_, func, or_

    return and_(
        or_(
            Asset.category.is_(None),
            func.lower(Asset.category).notin_((*NEVER_RANDOM_CATEGORIES, *ONE_MOMENT_CATEGORIES)),
        ),
        not_placeholder(),
        not_contract_audio(),
    )


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
    if analysis.get("version", 1) >= ANALYSIS_VERSION:
        return analysis
    return {k: analysis[k] for k in ("cue_in_seconds", "replay_gain_db", "loudness_lufs") if k in analysis}


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
