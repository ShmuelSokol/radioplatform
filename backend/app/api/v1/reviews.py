import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_manager
from app.db.session import get_db
from app.models.user import User
from app.models.review_queue import ReviewQueue, ReviewItem
from app.schemas.review import (
    BatchUpdateRequest,
    ReviewItemListResponse,
    ReviewItemResponse,
    ReviewItemUpdate,
    ReviewQueueCreate,
    ReviewQueueListResponse,
    ReviewQueueResponse,
)

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("/queues", response_model=ReviewQueueResponse, status_code=201)
async def create_queue(
    body: ReviewQueueCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    queue = ReviewQueue(
        name=body.name,
        description=body.description,
        created_by=user.id,
        total_items=len(body.asset_ids),
        reviewed_items=0,
    )
    db.add(queue)
    await db.flush()

    for i, asset_id in enumerate(body.asset_ids):
        item = ReviewItem(
            queue_id=queue.id,
            asset_id=uuid.UUID(asset_id),
            position=i + 1,
        )
        db.add(item)

    await db.commit()
    await db.refresh(queue)
    return queue


@router.get("/queues", response_model=ReviewQueueListResponse)
async def list_queues(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    count_result = await db.execute(select(func.count()).select_from(ReviewQueue))
    total = count_result.scalar() or 0
    result = await db.execute(
        select(ReviewQueue).offset(skip).limit(limit).order_by(ReviewQueue.created_at.desc())
    )
    queues = result.scalars().all()
    return ReviewQueueListResponse(queues=queues, total=total)


@router.get("/queues/{queue_id}", response_model=ReviewQueueResponse)
async def get_queue(
    queue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(ReviewQueue).where(ReviewQueue.id == queue_id))
    queue = result.scalar_one_or_none()
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found")
    return queue


@router.get("/queues/{queue_id}/items", response_model=ReviewItemListResponse)
async def list_queue_items(
    queue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ReviewItem)
        .where(ReviewItem.queue_id == queue_id)
        .order_by(ReviewItem.position)
    )
    items = result.scalars().all()
    return ReviewItemListResponse(items=items, total=len(items))


@router.get("/queues/{queue_id}/next", response_model=ReviewItemResponse | None)
async def get_next_item(
    queue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the next unreviewed item and assign to current user."""
    result = await db.execute(
        select(ReviewItem)
        .where(ReviewItem.queue_id == queue_id, ReviewItem.status == "pending")
        .order_by(ReviewItem.position)
        .limit(1)
    )
    item = result.scalar_one_or_none()
    if not item:
        return None
    item.status = "in_review"
    item.assigned_to = user.id
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=ReviewItemResponse)
async def update_item(
    item_id: uuid.UUID,
    body: ReviewItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(ReviewItem).where(ReviewItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.version != body.version:
        raise HTTPException(status_code=409, detail="Item was modified by another user")

    if body.status:
        item.status = body.status
    if body.notes is not None:
        item.notes = body.notes
    item.version += 1

    # Auto-create audit trail entry
    if body.status in ("approved", "rejected", "flagged"):
        from app.models.review_action import ReviewAction
        action = ReviewAction(
            review_item_id=item.id,
            asset_id=item.asset_id,
            user_id=user.id,
            action_type=body.status,
            comment=body.notes,
            details={"version": item.version},
        )
        db.add(action)

    # Update queue progress
    if body.status in ("approved", "rejected", "flagged"):
        queue_result = await db.execute(select(ReviewQueue).where(ReviewQueue.id == item.queue_id))
        queue = queue_result.scalar_one_or_none()
        if queue:
            count_result = await db.execute(
                select(func.count())
                .select_from(ReviewItem)
                .where(
                    ReviewItem.queue_id == queue.id,
                    ReviewItem.status.in_(["approved", "rejected", "flagged"]),
                )
            )
            queue.reviewed_items = count_result.scalar() or 0
            if queue.reviewed_items >= queue.total_items:
                queue.status = "completed"
            elif queue.status == "open":
                queue.status = "in_progress"

    await db.commit()
    await db.refresh(item)
    return item


@router.post("/queues/{queue_id}/batch-update")
async def batch_update(
    queue_id: uuid.UUID,
    body: BatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    updated = 0
    for item_id_str in body.item_ids:
        result = await db.execute(
            select(ReviewItem).where(
                ReviewItem.id == uuid.UUID(item_id_str),
                ReviewItem.queue_id == queue_id,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            item.status = body.status
            item.version += 1
            updated += 1

    # Update queue progress
    queue_result = await db.execute(select(ReviewQueue).where(ReviewQueue.id == queue_id))
    queue = queue_result.scalar_one_or_none()
    if queue:
        count_result = await db.execute(
            select(func.count())
            .select_from(ReviewItem)
            .where(
                ReviewItem.queue_id == queue.id,
                ReviewItem.status.in_(["approved", "rejected", "flagged"]),
            )
        )
        queue.reviewed_items = count_result.scalar() or 0
        if queue.reviewed_items >= queue.total_items:
            queue.status = "completed"
        elif queue.status == "open":
            queue.status = "in_progress"

    await db.commit()
    return {"updated": updated}


# --- Phase 5: Audit trail endpoints ---

@router.get("/assets/{asset_id}/history")
async def get_asset_history(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from app.models.review_action import ReviewAction
    from app.models.user import User as UserModel
    result = await db.execute(
        select(ReviewAction, UserModel.email)
        .outerjoin(UserModel, ReviewAction.user_id == UserModel.id)
        .where(ReviewAction.asset_id == asset_id)
        .order_by(ReviewAction.created_at.desc())
    )
    rows = result.all()
    return [
        {
            "id": str(action.id),
            "review_item_id": str(action.review_item_id) if action.review_item_id else None,
            "asset_id": str(action.asset_id),
            "user_id": str(action.user_id),
            "action_type": action.action_type,
            "comment": action.comment,
            "details": action.details,
            "created_at": action.created_at.isoformat() if action.created_at else None,
            "user_email": email,
        }
        for action, email in rows
    ]


@router.post("/assets/{asset_id}/comment")
async def add_comment(
    asset_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.models.review_action import ReviewAction
    action = ReviewAction(
        asset_id=asset_id,
        user_id=user.id,
        action_type="comment",
        comment=body.get("comment", ""),
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)
    return {"id": str(action.id), "status": "created"}


@router.get("/queues/{queue_id}/activity")
async def get_queue_activity(
    queue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from app.models.review_action import ReviewAction
    from app.models.user import User as UserModel
    # Get all item IDs for this queue
    items_result = await db.execute(
        select(ReviewItem.id).where(ReviewItem.queue_id == queue_id)
    )
    item_ids = [row[0] for row in items_result.all()]
    if not item_ids:
        return []

    result = await db.execute(
        select(ReviewAction, UserModel.email)
        .outerjoin(UserModel, ReviewAction.user_id == UserModel.id)
        .where(ReviewAction.review_item_id.in_(item_ids))
        .order_by(ReviewAction.created_at.desc())
        .limit(50)
    )
    rows = result.all()
    return [
        {
            "id": str(action.id),
            "review_item_id": str(action.review_item_id) if action.review_item_id else None,
            "asset_id": str(action.asset_id),
            "user_id": str(action.user_id),
            "action_type": action.action_type,
            "comment": action.comment,
            "details": action.details,
            "created_at": action.created_at.isoformat() if action.created_at else None,
            "user_email": email,
        }
        for action, email in rows
    ]
