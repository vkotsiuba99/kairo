"""
Сервіс Telemetry: події реакцій і винагорода для політики.

Лічильники приходять подією post.metrics від Publishing як абсолютні значення,
тому приріст рахується порівнянням із уже збереженим. Це ідемпотентно: повторне
опитування або повторна доставка події нічого не подвоює.

Винагорода відправляється подією reward.computed — її записує сервіс Content,
якому належить стан політики. Реакція аудиторії приходить із запізненням, тож
асинхронна доставка тут природна: політика й так оновлюється відкладено.
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.feedback import compute_reward
from app.events import emit
from app.models.entities import TelemetryEvent, TrackedPost

COUNTER_TO_EVENT = {
    "views": "view",
    "reactions": "reaction",
    "comments": "comment",
    "shares": "share",
    "saves": "save",
}

BASE_WEIGHTS = {
    "views": 0.15,
    "reactions": 0.35,
    "comments": 0.25,
    "saves": 0.15,
    "shares": 0.10,
}


async def track(db: AsyncSession, payload: Dict) -> TrackedPost:
    """Створює або оновлює запис про опублікований допис."""
    tracked = await db.get(TrackedPost, payload["post_id"])
    if tracked is None:
        tracked = TrackedPost(post_id=payload["post_id"])
        db.add(tracked)
    tracked.content_item_id = payload["content_item_id"]
    tracked.platform_code = payload["platform_code"]
    if payload.get("external_post_id"):
        tracked.external_post_id = payload["external_post_id"]
    if payload.get("counter_metrics") is not None:
        tracked.counter_metrics = list(payload["counter_metrics"])
    if payload.get("published_at"):
        tracked.published_at = datetime.fromisoformat(payload["published_at"])
    return tracked


async def recorded_totals(db: AsyncSession, post_id: int) -> Dict[str, float]:
    """Скільки телеметрії кожного типу вже записано для допису."""
    rows = await db.execute(
        select(TelemetryEvent.event_type, func.sum(TelemetryEvent.event_value))
        .where(TelemetryEvent.post_id == post_id)
        .group_by(TelemetryEvent.event_type)
    )
    return {event_type: float(total or 0) for event_type, total in rows.all()}


async def apply_counters(
    db: AsyncSession, tracked: TrackedPost, counters: Dict[str, float], source: str
) -> int:
    """Записує приріст лічильників як події телеметрії. Повертає їх кількість."""
    recorded = await recorded_totals(db, tracked.post_id)
    now = datetime.now(timezone.utc)
    written = 0
    for metric, absolute in (counters or {}).items():
        event_type = COUNTER_TO_EVENT.get(metric)
        if not event_type:
            continue
        delta = float(absolute or 0) - recorded.get(event_type, 0.0)
        if delta <= 0:
            continue
        db.add(
            TelemetryEvent(
                post_id=tracked.post_id,
                platform_code=tracked.platform_code,
                event_type=event_type,
                event_value=delta,
                occurred_at=now,
                raw_payload={"source": source, "absolute_total": absolute},
            )
        )
        written += 1
    if written:
        await db.flush()
    return written


def reward_weights(counter_metrics: Iterable[str]) -> Dict[str, float]:
    """
    Ваги нормуються за сигналами, доступними на конкретній платформі: у
    Mastodon і Bluesky переглядів немає в API взагалі, і без такої поправки
    вони були б систематично «гіршими» за YouTube на порожньому місці.
    """
    metrics = list(counter_metrics or [])
    available = [m for m in BASE_WEIGHTS if not metrics or m in metrics]
    weight_sum = sum(BASE_WEIGHTS[m] for m in available) or 1.0
    return {**BASE_WEIGHTS, **{m: BASE_WEIGHTS[m] / weight_sum for m in available}}


async def recompute_reward(db: AsyncSession, tracked: TrackedPost) -> Optional[float]:
    """Перераховує винагороду за всією телеметрією допису й надсилає її в Content."""
    totals = await recorded_totals(db, tracked.post_id)
    reward = compute_reward(
        views=totals.get("view", 0.0),
        reactions=totals.get("reaction", 0.0),
        comments=totals.get("comment", 0.0),
        saves=totals.get("save", 0.0),
        shares=totals.get("share", 0.0),
        weights=reward_weights(tracked.counter_metrics),
    )
    emit(db, "reward.computed", {
        "post_id": tracked.post_id,
        "content_item_id": tracked.content_item_id,
        "reward": reward,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    })
    return reward
