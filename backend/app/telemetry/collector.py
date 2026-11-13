"""
Збір телеметрії з підключених каналів.

Два незалежні джерела, бо вони відповідають на різні питання:

  * агрегатні лічильники допису ("скільки всього вподобань") — з них рахується
    винагорода для політики;
  * пособітійні взаємодії ("хто саме відповів") — з них будуються профілі
    аудиторії, без яких кластеризація не має на чому працювати.

Лічильники платформи віддають як абсолютні значення, тому приріст рахується
порівнянням із уже збереженим у базі. Такий підхід ідемпотентний: повторний
опит нічого не подвоює, а перезапуск сервісу не потребує окремого стану.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import ActorInteraction, BaseAdapter
from app.adapters.registry import get_adapter_for_account
from app.audience.embedding import get_embedding
from app.decision.feedback import compute_reward
from app.decision.policy_state import invalidate as invalidate_policy
from app.models.entities import (
    AudienceProfile,
    ConnectedAccount,
    ContentItem,
    PolicyDecision,
    PolicyReward,
    Post,
    TelemetryEvent,
)

logger = logging.getLogger("kairo.telemetry")

# Вікно, протягом якого має сенс опитувати допис (розділ 5.5 ТЗ)
FEEDBACK_WINDOW_HOURS = 48

COUNTER_TO_EVENT = {
    "views": "view",
    "reactions": "reaction",
    "comments": "comment",
    "shares": "share",
    "saves": "save",
}


async def _recorded_totals(db: AsyncSession, post_id: int) -> Dict[str, float]:
    """Скільки телеметрії кожного типу вже записано для допису."""
    rows = await db.execute(
        select(TelemetryEvent.event_type, func.sum(TelemetryEvent.event_value))
        .where(TelemetryEvent.post_id == post_id)
        .group_by(TelemetryEvent.event_type)
    )
    return {event_type: float(total or 0) for event_type, total in rows.all()}


async def _apply_counters(
    db: AsyncSession,
    post: Post,
    platform_id: int,
    counters: Dict[str, float],
    source: str,
) -> int:
    """Записує приріст лічильників як події телеметрії. Повертає їх кількість."""
    if not counters:
        return 0

    recorded = await _recorded_totals(db, post.id)
    written = 0
    now = datetime.now(timezone.utc)

    for metric, absolute in counters.items():
        event_type = COUNTER_TO_EVENT.get(metric)
        if not event_type:
            continue
        delta = float(absolute or 0) - recorded.get(event_type, 0.0)
        if delta <= 0:
            continue
        db.add(
            TelemetryEvent(
                post_id=post.id,
                platform_id=platform_id,
                event_type=event_type,
                event_value=delta,
                occurred_at=now,
                raw_payload={"source": source, "absolute_total": absolute},
            )
        )
        written += 1

    return written


async def recompute_reward_for_post(db: AsyncSession, post: Post) -> Optional[float]:
    """
    Перераховує винагороду за всією телеметрією допису.

    Ваги нормуються за сигналами, доступними на конкретній платформі: у
    Mastodon і Bluesky переглядів немає в API взагалі, і без такої поправки
    вони були б систематично «гіршими» за YouTube на порожньому місці.
    """
    if not post.content_item_id:
        return None

    decision = (
        await db.execute(
            select(PolicyDecision).where(PolicyDecision.content_item_id == post.content_item_id)
        )
    ).scalar_one_or_none()
    if not decision:
        return None

    totals = await _recorded_totals(db, post.id)

    adapter_metrics: List[str] = []
    if post.connected_account and post.connected_account.platform:
        adapter_cls = type(get_adapter_for_account(post.connected_account))
        adapter_metrics = list(getattr(adapter_cls, "COUNTER_METRICS", []) or [])

    base_weights = {
        "views": 0.15,
        "reactions": 0.35,
        "comments": 0.25,
        "saves": 0.15,
        "shares": 0.10,
    }
    available = [m for m in base_weights if not adapter_metrics or m in adapter_metrics]
    weight_sum = sum(base_weights[m] for m in available) or 1.0
    weights = {m: base_weights[m] / weight_sum for m in available}

    reward = compute_reward(
        views=totals.get("view", 0.0),
        reactions=totals.get("reaction", 0.0),
        comments=totals.get("comment", 0.0),
        saves=totals.get("save", 0.0),
        shares=totals.get("share", 0.0),
        weights={**base_weights, **weights},
    )

    db.add(
        PolicyReward(
            policy_decision_id=decision.id,
            reward=reward,
            computed_at=datetime.now(timezone.utc),
        )
    )
    return reward


async def _upsert_audience_profile(
    db: AsyncSession,
    account_id: int,
    interaction: ActorInteraction,
) -> bool:
    """
    Створює або оновлює профіль підписника за його взаємодією.

    Вектор інтересів будується з текстів, з якими людина взаємодіяла, — саме
    так, як описано в розділі 5.2 ТЗ. Новий текст усереднюється з наявним
    вектором, тому профіль поступово уточнюється, а не переписується начисто.
    """
    if not interaction.external_user_id:
        return False

    profile = (
        await db.execute(
            select(AudienceProfile).where(
                AudienceProfile.connected_account_id == account_id,
                AudienceProfile.external_user_id == interaction.external_user_id,
            )
        )
    ).scalar_one_or_none()

    text = (interaction.text or "").strip()
    embedding = await get_embedding(text) if text else None

    features = {
        "display_name": interaction.display_name,
        "last_interaction": interaction.interaction_type,
        "last_seen": interaction.occurred_at.isoformat(),
    }

    if profile is None:
        db.add(
            AudienceProfile(
                connected_account_id=account_id,
                external_user_id=interaction.external_user_id,
                first_seen=interaction.occurred_at,
                interest_embedding=embedding,
                engagement_features={**features, "interactions": 1},
            )
        )
        return True

    previous = profile.engagement_features or {}
    count = int(previous.get("interactions", 0)) + 1
    profile.engagement_features = {**previous, **features, "interactions": count}

    if embedding:
        if profile.interest_embedding and len(profile.interest_embedding) == len(embedding):
            profile.interest_embedding = [
                (old * (count - 1) + new) / count
                for old, new in zip(profile.interest_embedding, embedding)
            ]
        else:
            profile.interest_embedding = embedding
    return False


async def collect_for_account(db: AsyncSession, account: ConnectedAccount) -> Dict[str, int]:
    """Опитує один підключений канал і повертає підсумок зробленого."""
    adapter: BaseAdapter = get_adapter_for_account(account)
    stats = {"events": 0, "posts_polled": 0, "profiles_new": 0, "interactions": 0}

    if not getattr(type(adapter), "AVAILABLE", True):
        return stats

    since = datetime.now(timezone.utc) - timedelta(hours=FEEDBACK_WINDOW_HOURS)

    posts = (
        await db.execute(
            select(Post)
            .options(
                selectinload(Post.content_item),
                selectinload(Post.connected_account).selectinload(ConnectedAccount.platform),
            )
            .where(
                Post.connected_account_id == account.id,
                Post.status == "published",
                Post.published_at.isnot(None),
                Post.published_at >= since,
            )
        )
    ).scalars().all()

    touched_posts = set()

    # 1. Лічильники по кожному допису у вікні зворотного звʼязку
    for post in posts:
        if not post.external_post_id:
            continue
        counters = await adapter.fetch_post_metrics(post.external_post_id)
        stats["posts_polled"] += 1
        written = await _apply_counters(db, post, account.platform_id, counters, "poll")
        if written:
            stats["events"] += written
            touched_posts.add(post.id)

    # 2. Лічильники, які платформа віддає пачкою (Telegram)
    channel_counters = await adapter.fetch_channel_counters()
    if channel_counters:
        by_external = {p.external_post_id: p for p in posts if p.external_post_id}
        for external_id, counters in channel_counters.items():
            post = by_external.get(str(external_id))
            if not post:
                continue
            written = await _apply_counters(db, post, account.platform_id, counters, "updates")
            if written:
                stats["events"] += written
                touched_posts.add(post.id)

    # 3. Профілі аудиторії з пособітійних взаємодій
    if getattr(type(adapter), "SUPPORTS_PER_ACTOR", False):
        interactions = await adapter.fetch_interactions(since)
        stats["interactions"] = len(interactions)
        for interaction in interactions:
            created = await _upsert_audience_profile(db, account.id, interaction)
            if created:
                stats["profiles_new"] += 1

    # 4. Перерахунок винагороди для дописів, у яких зʼявилася нова телеметрія
    for post in posts:
        if post.id in touched_posts:
            await recompute_reward_for_post(db, post)

    await db.commit()
    if touched_posts:
        invalidate_policy()  # нова винагорода — політика перебудується з БД
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

    total = {"events": 0, "posts_polled": 0, "profiles_new": 0, "interactions": 0, "accounts": 0}
    for account in accounts:
        try:
            stats = await collect_for_account(db, account)
        except Exception as e:
            logger.warning(
                "Telemetry collection for %s failed: %s", account.external_account_id, e
            )
            await db.rollback()
            continue
        total["accounts"] += 1
        for key in ("events", "posts_polled", "profiles_new", "interactions"):
            total[key] += stats[key]

    return total
