"""
Маршрути сервісу Scheduler: черга публікацій, розклади каналів, підбір слотів.

Матеріал (Content), канали (Publishing) і сегменти (Content) належать іншим
сервісам і читаються через їхні REST-інтерфейси. У черзі зберігається копія
матеріалу на момент постановки — так публікація не залежить від доступності
Content.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audience.profiles import peak_hours, suggest_slots, timezone_offset
from app.core.activity import iso
from app.core.clients import ServiceError, call
from app.core.database import get_db
from app.models.entities import ChannelPlan, KnownChannel, Post
from app.scheduler.planner import describe_slot, next_free_slot, normalize_schedule
from app.scheduler.runner import publish_post
from app.schemas.api import PostingSchedule, PostResponse, QueuePostRequest, SchedulePostRequest

router = APIRouter(prefix="/schedule", tags=["Post Scheduler"])


# ------------------------------------------------------------ дані інших сервісів
async def _content(item_id: int) -> Dict[str, Any]:
    try:
        return await call("content", "GET", f"/content/{item_id}")
    except ServiceError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail="Content item not found")
        raise


_saved_channels: tuple = ()  # що цей процес уже записав у known_channels


async def _accounts(db: AsyncSession) -> List[Dict[str, Any]]:
    """
    Канали з Publishing. Список зберігається у власній БД Scheduler (спільній для всіх реплік):
    якщо Publishing лежить, черга приймає дописи за ним, а публікацію виконає Publishing,
    коли повернеться.
    """
    global _saved_channels
    try:
        accounts = await call("publishing", "GET", "/accounts")
    except ServiceError as e:
        if e.status_code < 500:
            raise
        known = (await db.execute(select(KnownChannel))).scalars().all()
        if not known:
            raise
        return [{"id": k.account_id, "platform_code": k.platform_code, "external_account_id": k.label,
                 "status": k.status} for k in known]

    snapshot = tuple(sorted((a["id"], a["platform_code"], a.get("external_account_id"), a["status"])
                            for a in accounts))
    if snapshot != _saved_channels:  # пишемо лише коли список змінився, а не на кожен запит
        try:
            for account_id, code, label, status in snapshot:
                await db.merge(KnownChannel(account_id=account_id, platform_code=code, label=label, status=status,
                                            updated_at=datetime.now(timezone.utc)))
            await db.commit()
            _saved_channels = snapshot
        except IntegrityError:  # інша репліка записала ті самі канали одночасно
            await db.rollback()
    return accounts


async def _account(db: AsyncSession, account_id: int) -> Dict[str, Any]:
    acc = next((a for a in await _accounts(db) if a["id"] == account_id), None)
    if not acc:
        raise HTTPException(status_code=404, detail="Channel not found")
    return acc


async def _segments() -> List[Dict[str, Any]]:
    return await call("content", "GET", "/audience/segments")


async def _segment(segment_id: int) -> Dict[str, Any]:
    seg = next((s for s in await _segments() if s["id"] == segment_id), None)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    return seg


def _new_post(item: Dict[str, Any], acc: Dict[str, Any], scheduled_at: datetime, status: str = "pending") -> Post:
    return Post(
        content_item_id=item["id"],
        connected_account_id=acc["id"],
        platform_code=acc["platform_code"],
        account_label=acc.get("external_account_id"),
        format=item.get("format") or "text",
        title=item.get("title"),
        body=item.get("body") or "",
        hashtags=item.get("hashtags") or [],
        media_refs=item.get("media_refs") or {},
        scheduled_at=scheduled_at,
        status=status,
        attempts=0,
    )


def _response(p: Post) -> PostResponse:
    return PostResponse(
        id=p.id,
        content_item_id=p.content_item_id,
        connected_account_id=p.connected_account_id,
        scheduled_at=p.scheduled_at,
        published_at=p.published_at,
        external_post_id=p.external_post_id,
        status=p.status,
        error_message=p.error_message,
    )


async def _plan(db: AsyncSession, account_id: int) -> Optional[ChannelPlan]:
    return await db.get(ChannelPlan, account_id)


# ------------------------------------------------------------------ маршрути
@router.post("", response_model=PostResponse)
async def schedule_post(req: SchedulePostRequest, db: AsyncSession = Depends(get_db)):
    item = await _content(req.content_item_id)
    acc = await _account(db, req.connected_account_id)
    post = _new_post(item, acc, req.scheduled_at)
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return _response(post)


@router.get("", response_model=List[PostResponse])
async def list_scheduled_posts(status: str = None, limit: int = 200, db: AsyncSession = Depends(get_db)):
    # Без ліміту запит віддавав усю історію публікацій і дорожчав з кожним дописом
    stmt = select(Post).order_by(Post.scheduled_at.desc()).limit(max(1, min(limit, 1000)))
    if status:
        stmt = stmt.where(Post.status == status)
    return [_response(p) for p in (await db.execute(stmt)).scalars().all()]


async def _publish_now(db: AsyncSession, post: Post) -> Dict[str, Any]:
    result = await publish_post(db, post)
    if result.metadata.get("retry_later"):
        post.status = "pending"  # диспетчер опублікує, щойно Publishing повернеться
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="The publishing service is unavailable; the post stays in the queue and will be published later",
        )
    if not result.success:
        post.status = "failed"
    await db.commit()
    if not result.success:
        raise HTTPException(status_code=424, detail=result.error_message or "Publishing failed")
    return {
        "status": "success",
        "post_id": post.id,
        "published_at": post.published_at,
        "external_post_id": post.external_post_id,
        "url": result.url,
    }


@router.post("/{post_id}/publish_now")
async def trigger_publish_now(post_id: int, db: AsyncSession = Depends(get_db)):
    # Захоплюємо допис так само, як диспетчер, — інакше натискання кнопки
    # під час його циклу дало б подвійну публікацію.
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
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one()
    return await _publish_now(db, post)


@router.get("/suggest")
async def suggest_publication_slots(segment_id: int, days: int = 3, per_day: int = 2):
    """
    Рекомендований час публікації для конкретного сегмента.

    Планувальник не має власного уявлення про «universally кращий час»: він
    бере криву добової активності сегмента (із сервісу Content) і повертає її
    піки, переведені в UTC.
    """
    seg = await _segment(segment_id)
    activity = seg.get("activity_hours") or []
    if not activity:
        raise HTTPException(
            status_code=400,
            detail="The segment has no activity curve — set up its profile on the Audience tab",
        )
    offset = timezone_offset(seg.get("profile") or {})
    return {
        "segment_id": seg["id"],
        "segment_label": seg["label"],
        "utc_offset": offset,
        "peak_hours": peak_hours(activity),
        "activity_hours": activity,
        "slots": suggest_slots(activity, offset, days=days, per_day=per_day),
    }


@router.get("/plans/{account_id}")
async def get_posting_plan(account_id: int, db: AsyncSession = Depends(get_db)):
    """Розклад публікацій каналу (власний або автоматичний за кривою сегмента)."""
    await _account(db, account_id)
    plan = await _plan(db, account_id)
    return normalize_schedule(plan.schedule if plan else None)


@router.put("/plans/{account_id}")
async def save_posting_plan(account_id: int, plan: PostingSchedule, db: AsyncSession = Depends(get_db)):
    await _account(db, account_id)
    if plan.mode == "auto" and plan.segment_id:
        await _segment(plan.segment_id)
    row = await _plan(db, account_id)
    if row is None:
        row = ChannelPlan(account_id=account_id)
        db.add(row)
    row.schedule = plan.model_dump()
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return row.schedule


async def _enqueue_by_plan(db: AsyncSession, item: Dict[str, Any], acc: Dict[str, Any]) -> Dict[str, Any]:
    row = await _plan(db, acc["id"])
    plan = normalize_schedule(row.schedule if row else None)
    activity = None
    if plan["mode"] == "auto":
        segments = await _segments()
        seg = next((s for s in segments if s["id"] == plan["segment_id"]), None) if plan["segment_id"] else None
        if seg is None:  # сегмент не вказано — беремо перший з кривою активності
            seg = next((s for s in sorted(segments, key=lambda s: s["id"]) if s.get("activity_hours")), None)
        if not seg or not seg.get("activity_hours"):
            raise HTTPException(status_code=400, detail="Auto schedule needs a segment with an activity curve")
        activity = seg["activity_hours"]
        plan["utc_offset"] = timezone_offset(seg.get("profile") or {})

    now = datetime.now(timezone.utc)
    taken = (
        await db.execute(
            select(Post.scheduled_at).where(
                Post.connected_account_id == acc["id"],
                Post.status.in_(("pending", "publishing", "published")),
                Post.scheduled_at >= now - timedelta(days=1),
            )
        )
    ).scalars().all()
    slot = next_free_slot(plan, list(taken), now=now, activity_hours=activity)
    if slot is None:
        raise HTTPException(status_code=409, detail="No free slots in the schedule for the next 3 weeks")

    post = _new_post(item, acc, slot)
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return {"post": _response(post), "mode": plan["mode"], "slot_label": describe_slot(slot, plan["utc_offset"])}


@router.post("/queue")
async def queue_by_plan(req: QueuePostRequest, db: AsyncSession = Depends(get_db)):
    """
    Ставить матеріал у чергу на найближчий вільний слот розкладу каналу.
    У режимі auto слоти — піки кривої активності сегмента з розкладу.
    """
    acc = await _account(db, req.connected_account_id)
    item = await _content(req.content_item_id)
    return await _enqueue_by_plan(db, item, acc)


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


# --------------------------------------------- внутрішні маршрути для інших сервісів
class EnqueueRequest(BaseModel):
    content_item: Dict[str, Any]          # матеріал у форматі GET /content/{id}
    publish_now: bool = False
    delay_minutes: Optional[int] = None   # None і не publish_now — за розкладом каналу


@router.post("/internal/enqueue")
async def enqueue_from_content(req: EnqueueRequest, db: AsyncSession = Depends(get_db)):
    """Постановка матеріалу в чергу з Content (студія «Опублікувати», автопланування)."""
    item = req.content_item
    acc = next(
        (a for a in await _accounts(db) if a["platform_code"] == item["platform_code"] and a["status"] == "active"),
        None,
    )
    if not acc:
        raise HTTPException(status_code=400, detail=f"No connected account for {item['platform_code']}")

    if not req.publish_now and req.delay_minutes is None:
        return await _enqueue_by_plan(db, item, acc)

    now = datetime.now(timezone.utc)
    if req.publish_now:
        post = _new_post(item, acc, now, status="publishing")
        post.claimed_at = now
        db.add(post)
        await db.flush()
        return await _publish_now(db, post)

    post = _new_post(item, acc, now + timedelta(minutes=req.delay_minutes))
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return {"post": _response(post)}


@router.get("/internal/stats")
async def queue_stats(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Post.id)))).scalar() or 0
    published = (await db.execute(select(func.count(Post.id)).where(Post.status == "published"))).scalar() or 0
    return {"total_posts": total, "published_posts": published}


@router.get("/internal/activity")
async def queue_activity(limit: int = 200, db: AsyncSession = Depends(get_db)):
    """Записи журналу подій про публікації (зводить Telemetry)."""
    posts = (await db.execute(select(Post).order_by(Post.id.desc()).limit(limit))).scalars().all()
    return [
        {
            "id": f"post-{p.id}",
            "at": iso(p.published_at or p.scheduled_at),
            "kind": "publication",
            "level": {"published": "success", "failed": "error"}.get(p.status, "info"),
            "title": f"Publication #{p.id}: {p.status}",
            "detail": p.error_message or p.title or "untitled",
            "meta": {"channel": p.account_label or "—", "external id": p.external_post_id or "—"},
        }
        for p in posts
    ]
