import logging
import random
import uuid
from datetime import datetime, timezone
from collections import Counter

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_manager
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)
from app.models.crm_member import CrmMember
from app.models.song_rating import SongRating
from app.models.raffle import Raffle, RaffleEntry
from app.models.asset import Asset
from app.schemas.crm import (
    LoginRequest,
    LoginResponse,
    MemberListResponse,
    MemberResponse,
    RaffleCreate,
    RaffleEntryResponse,
    RaffleResponse,
    RaffleUpdate,
    RateRequest,
    RatingResponse,
    RegisterRequest,
    RegisterResponse,
    SongRankingResponse,
    TasteProfile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crm", tags=["crm"])


# ── PIN helpers ────────────────────────────────────────────────

async def _generate_pin(db: AsyncSession) -> str:
    """Generate a unique 6-digit PIN, retrying on collision."""
    for _ in range(20):
        pin = f"{random.randint(0, 999999):06d}"
        existing = await db.execute(
            select(CrmMember.id).where(CrmMember.pin == pin).limit(1)
        )
        if not existing.scalar_one_or_none():
            return pin
    raise ConflictError("Could not generate unique PIN — please try again")


async def get_crm_member(
    x_crm_pin: str = Header(..., alias="X-CRM-PIN"),
    db: AsyncSession = Depends(get_db),
) -> CrmMember:
    """Dependency: authenticate a CRM member via PIN header."""
    result = await db.execute(
        select(CrmMember).where(CrmMember.pin == x_crm_pin, CrmMember.is_active.is_(True))
    )
    member = result.scalar_one_or_none()
    if not member:
        raise UnauthorizedError("Invalid PIN or inactive member")
    return member


# ── Taste Profile ──────────────────────────────────────────────

async def _compute_taste_profile(db: AsyncSession, member_id: uuid.UUID) -> TasteProfile:
    """Compute a fun taste profile from the member's ratings."""
    result = await db.execute(
        select(SongRating, Asset.category, Asset.asset_type)
        .join(Asset, SongRating.asset_id == Asset.id)
        .where(SongRating.member_id == member_id)
    )
    rows = result.all()

    total = len(rows)
    if total == 0:
        return TasteProfile(
            label="The Newcomer",
            description="Just getting started \u2014 keep rating!",
            top_category=None,
            stats={"total_ratings": 0, "favorites": 0},
        )

    favorites = [r for r in rows if r[0].is_favorite]
    fav_count = len(favorites)
    category_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()

    for row in rows:
        rating_obj, category, asset_type = row
        if category:
            category_counts[category] += 1
        if asset_type:
            type_counts[asset_type] += 1

    fav_categories: Counter[str] = Counter()
    for row in favorites:
        _, category, _ = row
        if category:
            fav_categories[category] += 1

    top_cat = category_counts.most_common(1)[0][0] if category_counts else None
    stats = {"total_ratings": total, "favorites": fav_count}

    if total < 5:
        return TasteProfile(
            label="The Newcomer",
            description="Just getting started \u2014 keep rating!",
            top_category=top_cat,
            stats=stats,
        )

    # Check profiles in priority order
    if fav_count > 0:
        fav_total = sum(fav_categories.values()) or 1
        lively_pct = fav_categories.get("lively", 0) / fav_total
        relax_pct = fav_categories.get("relax", 0) / fav_total

        if lively_pct > 0.5:
            return TasteProfile(
                label="The Simcha Machine",
                description="You light up every chasunah!",
                top_category="lively",
                stats=stats,
            )

        if relax_pct > 0.5:
            return TasteProfile(
                label="The Heartfelt Chazzan",
                description="Every niggun speaks to your neshama",
                top_category="relax",
                stats=stats,
            )

    shiur_pct = type_counts.get("shiur", 0) / total
    if shiur_pct > 0.4:
        return TasteProfile(
            label="The Yeshiva Yungelight",
            description="Learning is your jam \u2014 literally",
            top_category="shiur",
            stats=stats,
        )

    # Spread across 3+ categories evenly
    if len(category_counts) >= 3:
        vals = list(category_counts.values())
        max_pct = max(vals) / total
        if max_pct < 0.5:
            return TasteProfile(
                label="A True Harry",
                description="You can't be put in a box \u2014 you love it all!",
                top_category=top_cat,
                stats=stats,
            )

    if total > 30 and len(category_counts) >= 2:
        return TasteProfile(
            label="The Mixtape Maven",
            description="You've rated half the catalog \u2014 you're basically a DJ",
            top_category=top_cat,
            stats=stats,
        )

    return TasteProfile(
        label="The Music Lover",
        description="A true connoisseur of Jewish music",
        top_category=top_cat,
        stats=stats,
    )


# ══════════════════════════════════════════════════════════════
#  PUBLIC ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new CRM member. Returns a 6-digit PIN (shown once)."""
    if not data.name.strip():
        raise BadRequestError("Name is required")
    pin = await _generate_pin(db)
    member = CrmMember(name=data.name.strip(), pin=pin, phone=data.phone, email=data.email)
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return RegisterResponse(id=member.id, name=member.name, pin=member.pin)


@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with a 6-digit PIN."""
    result = await db.execute(
        select(CrmMember).where(CrmMember.pin == data.pin, CrmMember.is_active.is_(True))
    )
    member = result.scalar_one_or_none()
    if not member:
        raise UnauthorizedError("Invalid PIN or inactive member")

    profile = await _compute_taste_profile(db, member.id)

    fav_count = await db.execute(
        select(func.count()).select_from(SongRating).where(
            SongRating.member_id == member.id, SongRating.is_favorite.is_(True)
        )
    )
    rat_count = await db.execute(
        select(func.count()).select_from(SongRating).where(SongRating.member_id == member.id)
    )

    return LoginResponse(
        id=member.id,
        name=member.name,
        taste_profile=profile,
        favorites_count=fav_count.scalar() or 0,
        ratings_count=rat_count.scalar() or 0,
    )


@router.post("/rate", response_model=RatingResponse)
async def rate_song(
    data: RateRequest,
    member: CrmMember = Depends(get_crm_member),
    db: AsyncSession = Depends(get_db),
):
    """Rate or favorite a song. Upserts if a rating already exists."""
    # Verify asset exists
    asset = await db.execute(select(Asset).where(Asset.id == data.asset_id))
    asset_obj = asset.scalar_one_or_none()
    if not asset_obj:
        raise NotFoundError("Asset not found")

    # Check for existing rating
    existing = await db.execute(
        select(SongRating).where(
            SongRating.member_id == member.id, SongRating.asset_id == data.asset_id
        )
    )
    rating = existing.scalar_one_or_none()

    if rating:
        rating.rating = data.rating
        rating.is_favorite = data.is_favorite
    else:
        rating = SongRating(
            member_id=member.id,
            asset_id=data.asset_id,
            rating=data.rating,
            is_favorite=data.is_favorite,
        )
        db.add(rating)

    await db.flush()
    await db.refresh(rating)

    return RatingResponse(
        id=rating.id,
        asset_id=rating.asset_id,
        rating=rating.rating,
        is_favorite=rating.is_favorite,
        asset_title=asset_obj.title,
        asset_artist=asset_obj.artist,
        created_at=rating.created_at,
    )


@router.delete("/rate/{asset_id}", status_code=204)
async def remove_rating(
    asset_id: uuid.UUID,
    member: CrmMember = Depends(get_crm_member),
    db: AsyncSession = Depends(get_db),
):
    """Remove a rating for a song."""
    result = await db.execute(
        select(SongRating).where(
            SongRating.member_id == member.id, SongRating.asset_id == asset_id
        )
    )
    rating = result.scalar_one_or_none()
    if not rating:
        raise NotFoundError("Rating not found")
    await db.delete(rating)


@router.get("/my-ratings", response_model=list[RatingResponse])
async def my_ratings(
    member: CrmMember = Depends(get_crm_member),
    db: AsyncSession = Depends(get_db),
):
    """List all ratings/favorites for the authenticated member."""
    result = await db.execute(
        select(SongRating, Asset.title, Asset.artist)
        .join(Asset, SongRating.asset_id == Asset.id)
        .where(SongRating.member_id == member.id)
        .order_by(SongRating.created_at.desc())
    )
    rows = result.all()
    return [
        RatingResponse(
            id=r[0].id,
            asset_id=r[0].asset_id,
            rating=r[0].rating,
            is_favorite=r[0].is_favorite,
            asset_title=r[1],
            asset_artist=r[2],
            created_at=r[0].created_at,
        )
        for r in rows
    ]


@router.get("/raffles/active", response_model=list[RaffleResponse])
async def active_raffles(db: AsyncSession = Depends(get_db)):
    """List open raffles."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Raffle)
        .where(Raffle.status == "open", Raffle.starts_at <= now, Raffle.ends_at >= now)
        .order_by(Raffle.ends_at)
    )
    raffles = result.scalars().all()

    responses = []
    for r in raffles:
        entry_count = await db.execute(
            select(func.count()).select_from(RaffleEntry).where(RaffleEntry.raffle_id == r.id)
        )
        responses.append(
            RaffleResponse(
                id=r.id,
                title=r.title,
                description=r.description,
                prize=r.prize,
                station_id=r.station_id,
                starts_at=r.starts_at,
                ends_at=r.ends_at,
                status=r.status,
                entry_count=entry_count.scalar() or 0,
                created_at=r.created_at,
            )
        )
    return responses


@router.post("/raffles/{raffle_id}/enter", status_code=201)
async def enter_raffle(
    raffle_id: uuid.UUID,
    member: CrmMember = Depends(get_crm_member),
    db: AsyncSession = Depends(get_db),
):
    """Enter a raffle."""
    raffle = await db.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle_obj = raffle.scalar_one_or_none()
    if not raffle_obj:
        raise NotFoundError("Raffle not found")

    if raffle_obj.status != "open":
        raise BadRequestError("Raffle is not open")

    now = datetime.now(timezone.utc)
    if now < raffle_obj.starts_at or now > raffle_obj.ends_at:
        raise BadRequestError("Raffle is not active right now")

    # Check duplicate
    existing = await db.execute(
        select(RaffleEntry).where(
            RaffleEntry.raffle_id == raffle_id, RaffleEntry.member_id == member.id
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Already entered this raffle")

    entry = RaffleEntry(raffle_id=raffle_id, member_id=member.id)
    db.add(entry)
    await db.flush()
    return {"status": "entered", "raffle_id": str(raffle_id)}


# ══════════════════════════════════════════════════════════════
#  ADMIN ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.get("/members", response_model=MemberListResponse)
async def list_members(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """List CRM members with optional search."""
    stmt = select(CrmMember).where(CrmMember.is_active.is_(True))
    count_stmt = select(func.count()).select_from(CrmMember).where(CrmMember.is_active.is_(True))

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(CrmMember.name.ilike(pattern))
        count_stmt = count_stmt.where(CrmMember.name.ilike(pattern))

    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(
        stmt.order_by(CrmMember.created_at.desc()).offset(skip).limit(limit)
    )
    members = result.scalars().all()

    member_responses = []
    for m in members:
        # Get counts
        rat = await db.execute(
            select(func.count()).select_from(SongRating).where(SongRating.member_id == m.id)
        )
        fav = await db.execute(
            select(func.count()).select_from(SongRating).where(
                SongRating.member_id == m.id, SongRating.is_favorite.is_(True)
            )
        )
        profile = await _compute_taste_profile(db, m.id)
        member_responses.append(
            MemberResponse(
                id=m.id,
                name=m.name,
                pin=m.pin,
                phone=m.phone,
                email=m.email,
                is_active=m.is_active,
                taste_profile=profile,
                ratings_count=rat.scalar() or 0,
                favorites_count=fav.scalar() or 0,
                created_at=m.created_at,
            )
        )

    return MemberListResponse(members=member_responses, total=total)


@router.get("/members/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Get a single member detail."""
    result = await db.execute(select(CrmMember).where(CrmMember.id == member_id))
    m = result.scalar_one_or_none()
    if not m:
        raise NotFoundError("Member not found")

    rat = await db.execute(
        select(func.count()).select_from(SongRating).where(SongRating.member_id == m.id)
    )
    fav = await db.execute(
        select(func.count()).select_from(SongRating).where(
            SongRating.member_id == m.id, SongRating.is_favorite.is_(True)
        )
    )
    profile = await _compute_taste_profile(db, m.id)

    return MemberResponse(
        id=m.id,
        name=m.name,
        pin=m.pin,
        phone=m.phone,
        email=m.email,
        is_active=m.is_active,
        taste_profile=profile,
        ratings_count=rat.scalar() or 0,
        favorites_count=fav.scalar() or 0,
        created_at=m.created_at,
    )


@router.delete("/members/{member_id}", status_code=204)
async def deactivate_member(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Deactivate a CRM member (soft delete)."""
    result = await db.execute(select(CrmMember).where(CrmMember.id == member_id))
    m = result.scalar_one_or_none()
    if not m:
        raise NotFoundError("Member not found")
    m.is_active = False


@router.get("/song-rankings", response_model=list[SongRankingResponse])
async def song_rankings(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Songs ranked by listener ratings — useful for playlist curation."""
    result = await db.execute(
        select(
            SongRating.asset_id,
            Asset.title,
            Asset.artist,
            func.avg(SongRating.rating).label("avg_rating"),
            func.count(SongRating.id).label("total_ratings"),
            func.sum(
                func.cast(SongRating.is_favorite, Integer)
            ).label("favorite_count"),
        )
        .join(Asset, SongRating.asset_id == Asset.id)
        .group_by(SongRating.asset_id, Asset.title, Asset.artist)
        .order_by(func.avg(SongRating.rating).desc(), func.count(SongRating.id).desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.all()

    return [
        SongRankingResponse(
            asset_id=r.asset_id,
            title=r.title,
            artist=r.artist,
            avg_rating=round(float(r.avg_rating), 2),
            total_ratings=r.total_ratings,
            favorite_count=r.favorite_count or 0,
        )
        for r in rows
    ]


# ── Raffle Admin ───────────────────────────────────────────────

@router.get("/raffles", response_model=list[RaffleResponse])
async def list_raffles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """List all raffles (admin)."""
    result = await db.execute(
        select(Raffle).order_by(Raffle.created_at.desc()).offset(skip).limit(limit)
    )
    raffles = result.scalars().all()

    responses = []
    for r in raffles:
        entry_count = await db.execute(
            select(func.count()).select_from(RaffleEntry).where(RaffleEntry.raffle_id == r.id)
        )
        winner_name = None
        if r.winner_id:
            w = await db.execute(select(CrmMember.name).where(CrmMember.id == r.winner_id))
            winner_name = w.scalar_one_or_none()

        responses.append(
            RaffleResponse(
                id=r.id,
                title=r.title,
                description=r.description,
                prize=r.prize,
                station_id=r.station_id,
                starts_at=r.starts_at,
                ends_at=r.ends_at,
                status=r.status,
                winner_id=r.winner_id,
                winner_name=winner_name,
                entry_count=entry_count.scalar() or 0,
                created_at=r.created_at,
            )
        )
    return responses


@router.post("/raffles", response_model=RaffleResponse, status_code=201)
async def create_raffle(
    data: RaffleCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Create a new raffle."""
    raffle = Raffle(**data.model_dump())
    db.add(raffle)
    await db.flush()
    await db.refresh(raffle)
    return RaffleResponse(
        id=raffle.id,
        title=raffle.title,
        description=raffle.description,
        prize=raffle.prize,
        station_id=raffle.station_id,
        starts_at=raffle.starts_at,
        ends_at=raffle.ends_at,
        status=raffle.status,
        entry_count=0,
        created_at=raffle.created_at,
    )


@router.patch("/raffles/{raffle_id}", response_model=RaffleResponse)
async def update_raffle(
    raffle_id: uuid.UUID,
    data: RaffleUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Update a raffle."""
    result = await db.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = result.scalar_one_or_none()
    if not raffle:
        raise NotFoundError("Raffle not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(raffle, key, value)

    await db.flush()
    await db.refresh(raffle)

    entry_count = await db.execute(
        select(func.count()).select_from(RaffleEntry).where(RaffleEntry.raffle_id == raffle.id)
    )
    winner_name = None
    if raffle.winner_id:
        w = await db.execute(select(CrmMember.name).where(CrmMember.id == raffle.winner_id))
        winner_name = w.scalar_one_or_none()

    return RaffleResponse(
        id=raffle.id,
        title=raffle.title,
        description=raffle.description,
        prize=raffle.prize,
        station_id=raffle.station_id,
        starts_at=raffle.starts_at,
        ends_at=raffle.ends_at,
        status=raffle.status,
        winner_id=raffle.winner_id,
        winner_name=winner_name,
        entry_count=entry_count.scalar() or 0,
        created_at=raffle.created_at,
    )


@router.post("/raffles/{raffle_id}/draw", response_model=RaffleResponse)
async def draw_raffle(
    raffle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """Randomly draw a winner for a raffle."""
    result = await db.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = result.scalar_one_or_none()
    if not raffle:
        raise NotFoundError("Raffle not found")

    if raffle.status != "open":
        raise BadRequestError("Can only draw from an open raffle")

    entries = await db.execute(
        select(RaffleEntry).where(RaffleEntry.raffle_id == raffle_id)
    )
    all_entries = entries.scalars().all()
    if not all_entries:
        raise BadRequestError("No entries in this raffle")

    winner_entry = random.choice(all_entries)
    raffle.winner_id = winner_entry.member_id
    raffle.status = "drawn"

    await db.flush()
    await db.refresh(raffle)

    winner = await db.execute(select(CrmMember.name).where(CrmMember.id == raffle.winner_id))
    winner_name = winner.scalar_one_or_none()

    entry_count = await db.execute(
        select(func.count()).select_from(RaffleEntry).where(RaffleEntry.raffle_id == raffle.id)
    )

    return RaffleResponse(
        id=raffle.id,
        title=raffle.title,
        description=raffle.description,
        prize=raffle.prize,
        station_id=raffle.station_id,
        starts_at=raffle.starts_at,
        ends_at=raffle.ends_at,
        status=raffle.status,
        winner_id=raffle.winner_id,
        winner_name=winner_name,
        entry_count=entry_count.scalar() or 0,
        created_at=raffle.created_at,
    )


@router.get("/raffles/{raffle_id}/entries", response_model=list[RaffleEntryResponse])
async def raffle_entries(
    raffle_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_manager),
):
    """List entries for a raffle."""
    result = await db.execute(
        select(RaffleEntry, CrmMember.name)
        .join(CrmMember, RaffleEntry.member_id == CrmMember.id)
        .where(RaffleEntry.raffle_id == raffle_id)
        .order_by(RaffleEntry.created_at)
    )
    rows = result.all()
    return [
        RaffleEntryResponse(
            id=r[0].id,
            member_id=r[0].member_id,
            member_name=r[1],
            created_at=r[0].created_at,
        )
        for r in rows
    ]

