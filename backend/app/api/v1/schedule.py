from datetime import datetime, timedelta, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.models.entities import Post, ContentItem, ConnectedAccount, AudienceSegment
from app.schemas.api import SchedulePostRequest, PostResponse, PostingSchedule, QueuePostRequest
from app.scheduler.runner import publish_post
from app.scheduler.planner import describe_slot, next_free_slot, normalize_schedule
from app.audience.profiles import suggest_slots, timezone_offset, peak_hours

router = APIRouter(prefix="/schedule", tags=["Post Scheduler"])


@router.post("", response_model=PostResponse)
async def schedule_post(req: SchedulePostRequest, db: AsyncSession = Depends(get_db)):
    # Verify content item
    ci_res = await db.execute(select(ContentItem).where(ContentItem.id == req.content_item_id))
    item = ci_res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")

    # Verify connected account
    acc_res = await db.execute(select(ConnectedAccount).where(ConnectedAccount.id == req.connected_account_id))
    acc = acc_res.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="Connected account not found")

    post = Post(
        content_item_id=req.content_item_id,
        connected_account_id=req.connected_account_id,
        scheduled_at=req.scheduled_at,
        status="pending",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    return PostResponse(
        id=post.id,
        content_item_id=post.content_item_id,
        connected_account_id=post.connected_account_id,
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        external_post_id=post.external_post_id,
        status=post.status,
        error_message=post.error_message,
    )


@router.get("", response_model=List[PostResponse])
async def list_scheduled_posts(status: str = None, limit: int = 200, db: AsyncSession = Depends(get_db)):
    # Без ліміту запит віддавав усю історію публікацій і дорожчав з кожним дописом
    stmt = select(Post).order_by(Post.scheduled_at.desc()).limit(max(1, min(limit, 1000)))
    if status:
        stmt = stmt.where(Post.status == status)
    res = await db.execute(stmt)
    posts = res.scalars().all()
    return [
        PostResponse(
            id=p.id,
            content_item_id=p.content_item_id,
            connected_account_id=p.connected_account_id,
            scheduled_at=p.scheduled_at,
            published_at=p.published_at,
            external_post_id=p.external_post_id,
            status=p.status,
            error_message=p.error_message,
        )
        for p in posts
    ]


@router.post("/{post_id}/publish_now")
async def trigger_publish_now(post_id: int, db: AsyncSession = Depends(get_db)):
    # Захоплюємо допис так само, як публікатор, — інакше натискання кнопки
    # під час циклу публікатора дало б подвійну публікацію.
    claimed = await db.execute(
        update(Post)
        .where(Post.id == post_id, Post.status.in_(("pending", "failed")))
        .values(status="publishing", claimed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    if claimed.rowcount != 1:
        exists = (await db.execute(select(Post.id).where(Post.id == post_id))).scalar_one_or_none()
        raise HTTPException(
            status_code=404 if exists is None else 409,
            detail="Post not found" if exists is None else "The post is already being published or is published",
        )

    post = (
        await db.execute(
            select(Post)
            .options(
                selectinload(Post.content_item).selectinload(ContentItem.platform),
                selectinload(Post.connected_account).selectinload(ConnectedAccount.platform),
            )
            .where(Post.id == post_id)
        )
    ).scalar_one()
    result = await publish_post(db, post)
    if not result.success:
        post.status = "failed"
    await db.commit()
    if not result.success:
        raise HTTPException(status_code=424, detail=result.error_message)
    return {
        "status": "success",
        "published_at": post.published_at,
        "external_post_id": post.external_post_id,
        "url": result.url,
    }


@router.get("/suggest")
async def suggest_publication_slots(
    segment_id: int,
    days: int = 3,
    per_day: int = 2,
    db: AsyncSession = Depends(get_db),
):
    """
    Рекомендований час публікації для конкретного сегмента.

    Планувальник не має власного уявлення про «universally кращий час»: він
    бере криву добової активності сегмента й повертає її піки, переведені в
    UTC. Тому офісна аудиторія отримує вечірні слоти, а сегмент із нічною
    активністю в іншому часовому поясі — свої власні.
    """
    seg = (
        await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id))
    ).scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    activity = seg.activity_hours or []
    if not activity:
        raise HTTPException(
            status_code=400,
            detail="The segment has no activity curve — set up its profile on the Audience tab",
        )

    offset = timezone_offset(seg.profile or {})
    return {
        "segment_id": seg.id,
        "segment_label": seg.label,
        "utc_offset": offset,
        "peak_hours": peak_hours(activity),
        "activity_hours": activity,
        "slots": suggest_slots(activity, offset, days=days, per_day=per_day),
    }


async def _account(db: AsyncSession, account_id: int) -> ConnectedAccount:
    acc = (
        await db.execute(select(ConnectedAccount).where(ConnectedAccount.id == account_id))
    ).scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="Channel not found")
    return acc


@router.get("/plans/{account_id}")
async def get_posting_plan(account_id: int, db: AsyncSession = Depends(get_db)):
    """Розклад публікацій каналу (власний або автоматичний за кривою сегмента)."""
    return normalize_schedule((await _account(db, account_id)).posting_schedule)


@router.put("/plans/{account_id}")
async def save_posting_plan(account_id: int, plan: PostingSchedule, db: AsyncSession = Depends(get_db)):
    acc = await _account(db, account_id)
    if plan.mode == "auto" and plan.segment_id:
        seg = (
            await db.execute(select(AudienceSegment).where(AudienceSegment.id == plan.segment_id))
        ).scalar_one_or_none()
        if not seg:
            raise HTTPException(status_code=404, detail="Segment not found")
    acc.posting_schedule = plan.model_dump()
    await db.commit()
    return acc.posting_schedule


@router.post("/queue")
async def queue_by_plan(req: QueuePostRequest, db: AsyncSession = Depends(get_db)):
    """
    Ставить матеріал у чергу на найближчий вільний слот розкладу каналу.
    У режимі auto слоти — піки кривої активності сегмента з розкладу.
    """
    acc = await _account(db, req.connected_account_id)
    item = (
        await db.execute(select(ContentItem).where(ContentItem.id == req.content_item_id))
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Post not found")

    plan = normalize_schedule(acc.posting_schedule)
    activity = None
    if plan["mode"] == "auto":
        seg = None
        if plan["segment_id"]:
            seg = (
                await db.execute(select(AudienceSegment).where(AudienceSegment.id == plan["segment_id"]))
            ).scalar_one_or_none()
        if seg is None:  # сегмент не вказано — беремо перший з кривою активності
            all_segments = (await db.execute(select(AudienceSegment).order_by(AudienceSegment.id))).scalars()
            seg = next((s for s in all_segments if s.activity_hours), None)
        if not seg or not seg.activity_hours:
            raise HTTPException(
                status_code=400,
                detail="Auto schedule needs a segment with an activity curve",
            )
        activity = seg.activity_hours
        plan["utc_offset"] = timezone_offset(seg.profile or {})

    now = datetime.now(timezone.utc)
    taken = (
        await db.execute(
            select(Post.scheduled_at).where(
                Post.connected_account_id == acc.id,
                Post.status.in_(("pending", "publishing", "published")),
                Post.scheduled_at >= now - timedelta(days=1),
            )
        )
    ).scalars().all()

    slot = next_free_slot(plan, list(taken), now=now, activity_hours=activity)
    if slot is None:
        raise HTTPException(status_code=409, detail="No free slots in the schedule for the next 3 weeks")

    post = Post(
        content_item_id=item.id,
        connected_account_id=acc.id,
        scheduled_at=slot,
        status="pending",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return {
        "post": PostResponse(
            id=post.id,
            content_item_id=post.content_item_id,
            connected_account_id=post.connected_account_id,
            scheduled_at=post.scheduled_at,
            published_at=post.published_at,
            external_post_id=post.external_post_id,
            status=post.status,
            error_message=post.error_message,
        ),
        "mode": plan["mode"],
        "slot_label": describe_slot(slot, plan["utc_offset"]),
    }


@router.post("/{post_id}/cancel")
async def cancel_scheduled_post(post_id: int, db: AsyncSession = Depends(get_db)):
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Publication not found")
    if post.status in ("published", "publishing"):
        raise HTTPException(status_code=400, detail="Already published or being published")
    post.status = "cancelled"
    await db.commit()
    return {"status": "cancelled", "id": post_id}
