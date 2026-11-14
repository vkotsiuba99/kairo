"""
Диспетчер черги сервісу Scheduler: черга публікацій поверх таблиці posts у БД сервісу.

Брокер (RabbitMQ) передає події між сервісами, але чергою дописів не є: публікації
заплановані на майбутній час, їх можна скасувати й перепланувати, а стан кожної
має бути видно в інтерфейсі — усе це вже є в таблиці posts. Тому таблиця і є чергою:

  pending ──claim──► publishing ──► published
     ▲                   │
     └── retry (backoff) ┴──► failed (після PUBLISH_MAX_ATTEMPTS)

Захоплення атомарне (UPDATE … WHERE status='pending'), тож кілька реплік
сервісу Scheduler не публікують той самий допис двічі; саму публікацію
виконує сервіс Publishing.
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

from app.adapters.base import PublishResult
from app.core.clients import ServiceError, call
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.entities import Post

logger = logging.getLogger("kairo.scheduler")


async def publish_post(db: AsyncSession, post: Post) -> PublishResult:
    """
    Єдина точка публікації допису (диспетчер черги, «опублікувати зараз», студія).

    Саму публікацію виконує сервіс Publishing (там облікові дані й адаптери);
    стан допису, повтори й захоплення лишаються тут. commit результату робить викликач.
    """
    # транзакція не тримається відкритою, поки чекаємо на інший сервіс
    await db.commit()
    try:
        res = await call("publishing", "POST", "/publishing/internal/publish", json={
            "post_id": post.id,
            "account_id": post.connected_account_id,
            "content_item_id": post.content_item_id,
            "title": post.title,
            "body": post.body,
            "hashtags": post.hashtags or [],
            "media_refs": post.media_refs or {},
            "format": post.format,
        })
        result = PublishResult(
            success=bool(res.get("success")),
            external_post_id=res.get("external_post_id"),
            url=res.get("url"),
            error_message=res.get("error_message"),
        )
    except ServiceError as e:  # Publishing недоступний — звичайна помилка спроби, буде повтор
        result = PublishResult(success=False, error_message=str(e.detail))

    post.attempts = (post.attempts or 0) + 1
    if result.success:
        post.status = "published"
        post.published_at = datetime.now(timezone.utc)
        post.external_post_id = result.external_post_id
        post.error_message = None
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
        post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one()
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
