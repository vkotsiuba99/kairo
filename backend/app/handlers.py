"""
Обробники подій між сервісами — уся асинхронна взаємодія в одному місці.

    Publishing ── post.published ──────► Telemetry (відстеження), Content (статус матеріалу)
    Publishing ── post.metrics ────────► Telemetry ── reward.computed ──► Content (політика)
    Publishing ── audience.interactions ► Content (профілі підписників)

Кожен обробник отримує сесію до БД свого сервісу; commit і запис про обробку
події робить app.events.dispatch в одній транзакції.
"""

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import handles
from app.models.entities import AudienceProfile, ContentItem, PolicyDecision, PolicyReward
from app.telemetry import service as telemetry


# ----------------------------------------------------------------- Telemetry
@handles("post.published", "telemetry")
async def track_published_post(db: AsyncSession, payload: Dict[str, Any]) -> None:
    await telemetry.track(db, payload)


@handles("post.metrics", "telemetry")
async def apply_post_metrics(db: AsyncSession, payload: Dict[str, Any]) -> None:
    # post.metrics несе все потрібне, тож порядок доставки відносно post.published не важливий
    tracked = await telemetry.track(db, payload)
    await db.flush()
    if await telemetry.apply_counters(db, tracked, payload.get("counters") or {}, payload.get("source", "poll")):
        await telemetry.recompute_reward(db, tracked)


# ------------------------------------------------------------------- Content
@handles("post.published", "content")
async def mark_content_published(db: AsyncSession, payload: Dict[str, Any]) -> None:
    await db.execute(
        update(ContentItem).where(ContentItem.id == payload["content_item_id"]).values(status="published")
    )


@handles("reward.computed", "content")
async def record_reward(db: AsyncSession, payload: Dict[str, Any]) -> None:
    from app.decision.policy_state import invalidate as invalidate_policy

    decision = (
        await db.execute(
            select(PolicyDecision).where(PolicyDecision.content_item_id == payload["content_item_id"])
        )
    ).scalars().first()
    if not decision:
        return  # матеріал створено не політикою (наприклад, вручну) — вчити нічого
    db.add(
        PolicyReward(
            policy_decision_id=decision.id,
            reward=payload["reward"],
            computed_at=datetime.fromisoformat(payload["computed_at"]),
        )
    )
    invalidate_policy()  # нова винагорода — політика перебудується з БД


@handles("audience.interactions", "content")
async def upsert_audience_profiles(db: AsyncSession, payload: Dict[str, Any]) -> None:
    """
    Профіль підписника за його взаємодіями.

    Вектор інтересів будується з текстів, з якими людина взаємодіяла (розділ 5.2
    ТЗ). Новий текст усереднюється з наявним вектором, тому профіль поступово
    уточнюється, а не переписується начисто.
    """
    from app.audience.embedding import get_embedding

    account_id = payload["account_id"]
    for item in payload.get("interactions") or []:
        user_id = item.get("external_user_id")
        if not user_id:
            continue
        profile = (
            await db.execute(
                select(AudienceProfile).where(
                    AudienceProfile.connected_account_id == account_id,
                    AudienceProfile.external_user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        text = (item.get("text") or "").strip()
        embedding = await get_embedding(text) if text else None
        features = {
            "display_name": item.get("display_name"),
            "last_interaction": item.get("interaction_type"),
            "last_seen": item.get("occurred_at"),
        }
        if profile is None:
            db.add(
                AudienceProfile(
                    connected_account_id=account_id,
                    external_user_id=user_id,
                    first_seen=datetime.fromisoformat(item["occurred_at"]),
                    interest_embedding=embedding,
                    engagement_features={**features, "interactions": 1},
                )
            )
            await db.flush()  # той самий підписник може трапитися в пакеті ще раз
            continue
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
