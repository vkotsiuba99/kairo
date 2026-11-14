"""
Сервіс Publishing: єдине місце, де зберігаються облікові дані каналів і живуть
адаптери мереж.

* publish() — одна спроба публікації допису (повтори й стан черги — у Scheduler);
  успіх фіксується в журналі publications і подією post.published.
* collect_all() — опитування лічильників опублікованих дописів і взаємодій
  підписників; результати йдуть подіями post.metrics та audience.interactions,
  а винагороду з них рахує Telemetry.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import BaseAdapter, PublishResult
from app.adapters.registry import get_adapter_for_account
from app.events import emit
from app.models.entities import ConnectedAccount, Publication

logger = logging.getLogger("kairo.publishing")

# Вікно, протягом якого має сенс опитувати допис (розділ 5.5 ТЗ)
FEEDBACK_WINDOW_HOURS = 48


async def load_account(db: AsyncSession, account_id: int) -> Optional[ConnectedAccount]:
    return (
        await db.execute(
            select(ConnectedAccount)
            .options(selectinload(ConnectedAccount.platform))
            .where(ConnectedAccount.id == account_id)
        )
    ).scalar_one_or_none()


def counter_metrics(adapter: BaseAdapter) -> list:
    return list(getattr(type(adapter), "COUNTER_METRICS", []) or [])


async def publish(db: AsyncSession, account: ConnectedAccount, post: Dict[str, Any]) -> PublishResult:
    """Публікує допис (копію матеріалу з черги Scheduler) через адаптер каналу."""
    adapter = get_adapter_for_account(account)
    try:
        result = await adapter.publish(
            content_item_id=post["content_item_id"],
            title=post.get("title"),
            body=post.get("body") or "",
            media_refs=post.get("media_refs") or {},
            hashtags=post.get("hashtags") or [],
            format_type=post.get("format") or "text",
        )
    except Exception as e:  # адаптер не повинен валити сервіс
        result = PublishResult(success=False, error_message=str(e))

    if result.success:
        now = datetime.now(timezone.utc)
        existing = (
            await db.execute(select(Publication).where(Publication.post_id == post["post_id"]))
        ).scalar_one_or_none()
        pub = existing or Publication(post_id=post["post_id"])
        pub.content_item_id = post["content_item_id"]
        pub.account_id = account.id
        pub.platform_code = account.platform.code
        pub.external_post_id = result.external_post_id
        pub.url = result.url
        pub.published_at = now
        if not existing:
            db.add(pub)
        emit(db, "post.published", {
            "post_id": post["post_id"],
            "content_item_id": post["content_item_id"],
            "account_id": account.id,
            "platform_code": account.platform.code,
            "external_post_id": result.external_post_id,
            "url": result.url,
            "published_at": now.isoformat(),
            "counter_metrics": counter_metrics(adapter),
        })
        await db.commit()
    return result


async def collect_for_account(db: AsyncSession, account: ConnectedAccount) -> Dict[str, int]:
    """Опитує один канал; повертає підсумок зробленого."""
    adapter = get_adapter_for_account(account)
    stats = {"posts_polled": 0, "metric_events": 0, "interactions": 0}
    if not getattr(type(adapter), "AVAILABLE", True):
        return stats

    since = datetime.now(timezone.utc) - timedelta(hours=FEEDBACK_WINDOW_HOURS)
    pubs = (
        await db.execute(
            select(Publication).where(
                Publication.account_id == account.id,
                Publication.published_at >= since,
            )
        )
    ).scalars().all()
    metrics = counter_metrics(adapter)

    def send(pub: Publication, counters: Dict[str, float], source: str) -> None:
        emit(db, "post.metrics", {
            "post_id": pub.post_id,
            "content_item_id": pub.content_item_id,
            "platform_code": pub.platform_code,
            "external_post_id": pub.external_post_id,
            "counters": counters,
            "counter_metrics": metrics,
            "source": source,
        })
        stats["metric_events"] += 1

    # 1. Лічильники по кожному допису у вікні зворотного зв'язку (абсолютні значення:
    #    приріст рахує Telemetry, тож повторне опитування нічого не подвоює)
    for pub in pubs:
        if not pub.external_post_id:
            continue
        counters = await adapter.fetch_post_metrics(pub.external_post_id)
        stats["posts_polled"] += 1
        if counters:
            send(pub, counters, "poll")

    # 2. Лічильники, які платформа віддає пачкою (Telegram)
    channel_counters = await adapter.fetch_channel_counters()
    if channel_counters:
        by_external = {p.external_post_id: p for p in pubs if p.external_post_id}
        for external_id, counters in channel_counters.items():
            pub = by_external.get(str(external_id))
            if pub and counters:
                send(pub, counters, "updates")

    # 3. Взаємодії підписників — з них Content будує профілі аудиторії
    if getattr(type(adapter), "SUPPORTS_PER_ACTOR", False):
        interactions = await adapter.fetch_interactions(since)
        stats["interactions"] = len(interactions)
        if interactions:
            emit(db, "audience.interactions", {
                "account_id": account.id,
                "interactions": [
                    {
                        "external_user_id": i.external_user_id,
                        "display_name": i.display_name,
                        "interaction_type": i.interaction_type,
                        "text": i.text,
                        "occurred_at": i.occurred_at.isoformat(),
                    }
                    for i in interactions
                ],
            })

    await db.commit()
    return stats


async def collect_all(db: AsyncSession) -> Dict[str, int]:
    """Один прохід по всіх активних каналах."""
    accounts = (
        await db.execute(
            select(ConnectedAccount)
            .options(selectinload(ConnectedAccount.platform))
            .where(ConnectedAccount.status == "active")
        )
    ).scalars().all()

    total = {"accounts": 0, "posts_polled": 0, "metric_events": 0, "interactions": 0}
    for account in accounts:
        try:
            stats = await collect_for_account(db, account)
        except Exception as e:
            logger.warning("Metrics collection for %s failed: %s", account.external_account_id, e)
            await db.rollback()
            continue
        total["accounts"] += 1
        for key in ("posts_polled", "metric_events", "interactions"):
            total[key] += stats[key]
    return total
