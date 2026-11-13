"""
Публікатор: черга публікацій поверх таблиці posts.

Окремий брокер (Kafka/Redpanda, RQ) тут не потрібен: публікації заплановані на
майбутній час, їх можна скасувати й перепланувати, а стан кожної має бути видно
в інтерфейсі — усе це вже є в таблиці posts. Тому таблиця і є чергою:

  pending ──claim──► publishing ──► published
     ▲                   │
     └── retry (backoff) ┴──► failed (після PUBLISH_MAX_ATTEMPTS)

Захоплення атомарне (UPDATE … WHERE status='pending'), тож кілька реплік
публікатора та планувальник усередині API не публікують той самий допис двічі.
Допис, захоплений воркером, який упав, повертається в чергу за тайм-аутом.
Гарантія — «щонайменше один раз»: падіння між відповіддю платформи і
фіксацією в БД може дати повтор.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import PublishResult
from app.adapters.registry import get_adapter_for_account
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.entities import ConnectedAccount, ContentItem, Post

logger = logging.getLogger("kairo.scheduler")


def _with_relations(stmt):
    return stmt.options(
        selectinload(Post.content_item).selectinload(ContentItem.platform),
        selectinload(Post.connected_account).selectinload(ConnectedAccount.platform),
    )


async def publish_post(db: AsyncSession, post: Post) -> PublishResult:
    """
    Єдина точка публікації допису (планувальник, «опублікувати зараз», студія).
    Оновлює стан допису й матеріалу; commit робить викликач.
    """
    account, item = post.connected_account, post.content_item
    if not account or not item:
        result = PublishResult(success=False, error_message="The post has no channel or content")
    else:
        try:
            result = await get_adapter_for_account(account).publish(
                content_item_id=item.id,
                title=item.title,
                body=item.body,
                media_refs=item.media_refs or {},
                hashtags=item.hashtags or [],
                format_type=item.format,
            )
        except Exception as e:  # адаптер не повинен валити воркер
            result = PublishResult(success=False, error_message=str(e))

    post.attempts = (post.attempts or 0) + 1
    if result.success:
        post.status = "published"
        post.published_at = datetime.now(timezone.utc)
        post.external_post_id = result.external_post_id
        post.error_message = None
        item.status = "published"
    else:
        post.error_message = result.error_message
    return result


async def claim_due_posts(db: AsyncSession, limit: int) -> List[int]:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=settings.PUBLISH_CLAIM_TIMEOUT_SECONDS)
    # Воркер упав посеред публікації — допис повертається в чергу
    await db.execute(
        update(Post)
        .where(Post.status == "publishing", Post.claimed_at < stale)
        .values(status="pending")
    )
    candidates = (
        await db.execute(
            select(Post.id)
            .where(Post.status == "pending", Post.scheduled_at <= now)
            .order_by(Post.scheduled_at)
            .limit(limit)
        )
    ).scalars().all()

    claimed: List[int] = []
    for post_id in candidates:
        res = await db.execute(
            update(Post)
            .where(Post.id == post_id, Post.status == "pending")
            .values(status="publishing", claimed_at=now)
        )
        if res.rowcount == 1:  # іншу репліку випередили — допис не наш
            claimed.append(post_id)
    await db.commit()
    return claimed


async def _publish_claimed(post_id: int, sem: asyncio.Semaphore) -> bool:
    async with sem, AsyncSessionLocal() as db:
        post = (await db.execute(_with_relations(select(Post).where(Post.id == post_id)))).scalar_one()
        result = await publish_post(db, post)
        if not result.success:
            if post.attempts < settings.PUBLISH_MAX_ATTEMPTS:
                delay = settings.PUBLISH_RETRY_BASE_SECONDS * 2 ** (post.attempts - 1)
                post.status = "pending"
                post.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                logger.warning("Post #%d: attempt %d failed (%s), retry in %d s",
                               post.id, post.attempts, result.error_message, delay)
            else:
                post.status = "failed"
                logger.warning("Post #%d not published after %d attempts: %s",
                               post.id, post.attempts, result.error_message)
        await db.commit()
        return result.success


async def publish_due_posts() -> int:
    """Один цикл публікатора. Повертає кількість успішно опублікованих дописів."""
    async with AsyncSessionLocal() as db:
        ids = await claim_due_posts(db, settings.PUBLISH_BATCH_SIZE)
    if not ids:
        return 0
    sem = asyncio.Semaphore(settings.PUBLISH_CONCURRENCY)
    results = await asyncio.gather(*(_publish_claimed(i, sem) for i in ids), return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    logger.info("Publisher: claimed %d, published %d", len(ids), ok)
    return ok


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        publish_due_posts, "interval", seconds=settings.SCHEDULER_INTERVAL_SECONDS,
        id="publish_due_posts", max_instances=1, coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started (interval: %ss)", settings.SCHEDULER_INTERVAL_SECONDS)
    return scheduler
