"""
Стан політики (матриці LinUCB) відтворюється з історії рішень і винагород у БД.

Раніше бандит жив лише в пам'яті процесу API і ніколи не отримував реальної
винагороди: телеметрія записувала PolicyReward, але модель його не бачила.
Тепер джерело істини — таблиці policy_decisions / policy_rewards:
  * кожна репліка API будує той самий стан (горизонтальне масштабування);
  * перезапуск або падіння процесу нічого не стирає;
  * відкладена винагорода враховується автоматично — для кожного рішення
    береться її останнє (повне) значення, а не сума проміжних.
"""

import time
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.decision.factorized_bandit import FactorizedContextualBandit
from app.models.entities import PolicyDecision, PolicyReward

# 16 ознак сегмента + 8 платформ + година, день, 3 ковзні статистики = 29 → 32 з запасом.
BASE_CONTEXT_DIM = 32
# Рішення попередньої версії мали інший склад контексту (5 платформ) — не змішуємо.
POLICY_VERSION = "factorized_linucb_v2"

_cache: Dict[str, object] = {"bandit": None, "built_at": 0.0, "decisions": 0}


def invalidate() -> None:
    """Нова винагорода в цьому процесі — стан перебудується під час наступного вибору."""
    _cache["built_at"] = 0.0


async def rebuild_bandit(db: AsyncSession) -> FactorizedContextualBandit:
    # Остання винагорода кожного рішення (рядки йдуть за часом — пізніший перезаписує)
    latest: Dict[int, float] = {}
    rows = await db.execute(
        select(PolicyReward.policy_decision_id, PolicyReward.reward).order_by(
            PolicyReward.computed_at, PolicyReward.id
        )
    )
    for decision_id, reward in rows.all():
        latest[decision_id] = float(reward)

    bandit = FactorizedContextualBandit(base_context_dim=BASE_CONTEXT_DIM)
    if latest:
        decisions = await db.execute(
            select(PolicyDecision.id, PolicyDecision.context_vector, PolicyDecision.chosen_arms).where(
                PolicyDecision.id.in_(list(latest)),
                PolicyDecision.policy_version == POLICY_VERSION,
            )
        )
        bandit.learn_batch(
            [(ctx or [], arms or {}, latest[did]) for did, ctx, arms in decisions.all()]
        )
    _cache.update(bandit=bandit, built_at=time.monotonic(), decisions=len(latest))
    return bandit


async def get_bandit(db: AsyncSession, max_age: Optional[float] = None) -> FactorizedContextualBandit:
    ttl = settings.BANDIT_STATE_TTL_SECONDS if max_age is None else max_age
    bandit = _cache["bandit"]
    if bandit is None or time.monotonic() - float(_cache["built_at"]) > ttl:
        bandit = await rebuild_bandit(db)
    return bandit  # type: ignore[return-value]


def learned_decisions() -> int:
    return int(_cache["decisions"])
