"""
Побудова добових ознак і індексу попиту.

Три пастки, кожна з яких перетворює артефакт збору на вигаданий сигнал,
якщо її не обробити тут.

1. Повнота збору змінюється в часі. Пагінація йде назад, поки не вичерпано
   бюджет сторінок, тому найстаріша доба зібрана частково — на ній обрив.
   Сьогоднішня теж неповна, вона ще триває. Обидві крайні доби відрізаємо,
   інакше модель вивчить «чим давніше, тим менше згадок» — а це не попит,
   а межа збирача.

2. Реакції цензуровані за віком. Лічильники знято в момент збору: у допису
   тижневої давнини вони остаточні, у вчорашнього ще набігають. Наївна сума
   дає «останні доби завжди слабші» — тобто модель занижуватиме свіжий попит
   саме там, де він потрібен. Рахуємо вподобання лише за дописами, яким на
   момент збору було щонайменше дві доби, і зберігаємо частку таких.

3. Доба без згадок — це справжній нуль, а не пропуск. Прогалини заповнюємо
   явно, інакше середні й похідні брешуть.

Одиниця обліку — автор, а не допис. Людина пише «хочу», потім «замовив»,
потім «прийшло»; у підрахунку за дописами вона врахована тричі, хоча попит
один. Тому автор зараховується один раз, на найдальшій досягнутій стадії.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand.annotator import FRESH_PURCHASE, REJECTION, STAGES, TRANSACTED, stage_rank
from app.demand.entities import DailyFeature, DemandProduct, MentionAnnotation, SocialMention
from app.demand.matcher import normalize_for_hash

logger = logging.getLogger("kairo.demand.features")

# Вік допису, після якого лічильники реакцій вважаємо усталеними.
ENGAGEMENT_MATURITY = timedelta(hours=48)

# Запасні ваги стадій — використовуються, лише поки емпіричних ще нема.
# Свідомо грубі: справжні рахуються за траєкторіями авторів.
FALLBACK_WEIGHTS: Dict[str, float] = {
    "none": 0.0,
    "awareness": 0.02,
    "interest": 0.10,
    "consideration": 0.25,
    "intent": 0.60,
    "ordered": 1.00,
    "received": 1.00,
    "usage": 0.15,
    "repurchase": 1.00,
    REJECTION: -0.60,
}


@dataclass
class StageWeights:
    weights: Dict[str, float] = field(default_factory=dict)
    observed: Dict[str, Tuple[int, int]] = field(default_factory=dict)  # стадія -> (дійшли, всього)
    horizon_days: int = 30
    empirical: bool = False

    def get(self, stage: str) -> float:
        return self.weights.get(stage, FALLBACK_WEIGHTS.get(stage, 0.0))


async def estimate_stage_weights(
    session: AsyncSession,
    horizon_days: int = 30,
    min_observations: int = 30,
    model_name: Optional[str] = None,
) -> StageWeights:
    """
    Емпірична вага стадії — частка авторів, які після неї дійшли до транзакції
    у вікні `horizon_days`.

    Так ваги не постулюються, а вимірюються, і заразом перевіряється саме
    припущення, що намір конвертується.

    Обмеження, яке треба тримати в голові: видно лише тих, хто написав удруге.
    Мовчазні покупці не потрапляють, тому це нижня оцінка. Для індексу цього
    досить, бо він відносний, але видавати ці числа за ймовірності покупки
    не можна.
    """
    stmt = (
        select(
            SocialMention.product_id,
            SocialMention.author_id,
            SocialMention.posted_at,
            MentionAnnotation.intent_stage,
        )
        .join(MentionAnnotation, MentionAnnotation.mention_id == SocialMention.id)
        .where(MentionAnnotation.relevant.is_(True))
    )
    if model_name:
        stmt = stmt.where(MentionAnnotation.model_name == model_name)
    rows = (await session.execute(stmt)).all()

    # Траєкторія: (товар, автор) -> [(момент, стадія), ...]
    journeys: Dict[Tuple[int, str], List[Tuple[datetime, str]]] = defaultdict(list)
    for pid, author, posted_at, stage in rows:
        if not author or not stage:
            continue
        journeys[(pid, author)].append((posted_at, stage))

    reached: Dict[str, int] = defaultdict(int)
    total: Dict[str, int] = defaultdict(int)
    horizon = timedelta(days=horizon_days)

    for events in journeys.values():
        events.sort(key=lambda e: e[0])
        for i, (when, stage) in enumerate(events):
            if stage in TRANSACTED or stage == REJECTION or stage == "none":
                continue
            total[stage] += 1
            if any(
                later_stage in TRANSACTED and 0 < (later_when - when) <= horizon
                for later_when, later_stage in events[i + 1:]
            ):
                reached[stage] += 1

    weights: Dict[str, float] = {}
    observed: Dict[str, Tuple[int, int]] = {}
    empirical = False
    for stage in STAGES:
        n = total.get(stage, 0)
        observed[stage] = (reached.get(stage, 0), n)
        if n >= min_observations:
            weights[stage] = round(reached.get(stage, 0) / n, 4)
            empirical = True

    # Свіжа покупка — одиниця за визначенням: транзакція щойно відбулась.
    for stage in FRESH_PURCHASE:
        weights[stage] = 1.0
    # «Користуюсь» лишається на виміряній або запасній вазі: це слід покупки,
    # яка вже врахована раніше, а не новий попит.
    weights.setdefault("usage", FALLBACK_WEIGHTS["usage"])
    weights["none"] = 0.0
    # Відмова віднімається з тією самою силою, з якою намір додавався:
    # людина, яка передумала, виходить із воронки рівно на стільки, на скільки
    # увійшла. Інакше індекс уміє лише зростати.
    weights[REJECTION] = -weights.get("intent", FALLBACK_WEIGHTS["intent"])

    return StageWeights(weights=weights, observed=observed,
                        horizon_days=horizon_days, empirical=empirical)


def complete_window(days_present: Sequence[date]) -> Tuple[Optional[date], Optional[date]]:
    """
    Вікно, у якому збір повний.

    Пагінація йде від сьогодні назад і обривається посеред найстарішої доби,
    тому та доба неповна. Сьогоднішня неповна з іншої причини — вона ще триває.
    Обидві відкидаємо.
    """
    if not days_present:
        return None, None
    lo, hi = min(days_present), max(days_present)
    today = datetime.now(timezone.utc).date()
    lo = lo + timedelta(days=1)
    hi = min(hi, today - timedelta(days=1))
    if lo > hi:
        return None, None
    return lo, hi


async def build_daily_features(
    session: AsyncSession,
    product: DemandProduct,
    weights: StageWeights,
    model_name: Optional[str] = None,
) -> int:
    """Рахує добові рядки для одного товару. Повертає кількість записаних діб."""
    rows = (await session.execute(
        select(
            SocialMention.id,
            SocialMention.author_id,
            SocialMention.posted_at,
            SocialMention.fetched_at,
            SocialMention.like_count,
            SocialMention.repost_count,
            SocialMention.reply_count,
            SocialMention.text_hash,
            SocialMention.looks_promo,
            SocialMention.is_bridged,
            MentionAnnotation.intent_stage,
            MentionAnnotation.scarcity,
            MentionAnnotation.sentiment,
            MentionAnnotation.is_ad,
            MentionAnnotation.relevant,
        )
        .outerjoin(
            MentionAnnotation,
            (MentionAnnotation.mention_id == SocialMention.id)
            & ((MentionAnnotation.model_name == model_name) if model_name else True),
        )
        .where(SocialMention.product_id == product.id)
    )).all()
    if not rows:
        return 0

    per_day: Dict[date, List[tuple]] = defaultdict(list)
    for r in rows:
        per_day[r.posted_at.date()].append(r)

    lo, hi = complete_window(list(per_day))
    if lo is None or hi is None:
        logger.info("%s: full-collection window is empty", product.code)
        return 0

    existing = {
        f.day: f for f in (await session.execute(
            select(DailyFeature).where(
                DailyFeature.product_id == product.id, DailyFeature.platform == "all"
            )
        )).scalars().all()
    }

    written = 0
    day = lo
    while day <= hi:
        items = per_day.get(day, [])
        # Міст дублює того самого автора з іншого майданчика — рахуємо один раз.
        items = [r for r in items if not r.is_bridged]

        authors_best: Dict[str, str] = {}
        scarcity_counts: Dict[str, int] = defaultdict(int)
        sentiments: List[float] = []
        hashes: List[str] = []
        promo = 0
        engagement = 0
        mature = 0

        for r in items:
            hashes.append(r.text_hash or "")
            if r.is_ad or r.looks_promo:
                promo += 1
            if r.sentiment is not None:
                sentiments.append(float(r.sentiment))
            if r.scarcity and r.scarcity != "none":
                scarcity_counts[r.scarcity] += 1
            if r.fetched_at and (r.fetched_at - r.posted_at) >= ENGAGEMENT_MATURITY:
                mature += 1
                engagement += (r.like_count or 0) + (r.repost_count or 0)
            if r.author_id and r.relevant is not False:
                stage = r.intent_stage or "none"
                current = authors_best.get(r.author_id)
                if current is None or stage_rank(stage) > stage_rank(current):
                    authors_best[r.author_id] = stage

        stage_authors: Dict[str, int] = defaultdict(int)
        for stage in authors_best.values():
            stage_authors[stage] += 1

        unique_authors = len(authors_best)
        shares = (
            {s: round(n / unique_authors, 4) for s, n in stage_authors.items()}
            if unique_authors else {}
        )
        demand_index = sum(weights.get(s) * n for s, n in stage_authors.items())

        row = existing.get(day)
        if row is None:
            row = DailyFeature(product_id=product.id, platform="all", day=day)
            session.add(row)
        row.mention_count = len(items)
        row.unique_authors = unique_authors
        row.intent_shares = shares
        row.scarcity_counts = dict(scarcity_counts)
        row.mean_sentiment = round(sum(sentiments) / len(sentiments), 4) if sentiments else None
        row.engagement_sum = engagement
        row.duplicate_ratio = (
            round(1 - len(set(hashes)) / len(hashes), 4) if hashes else 0.0
        )
        row.promo_ratio = round(promo / len(items), 4) if items else 0.0
        row.extras = {
            "demand_index": round(demand_index, 3),
            "stage_authors": dict(stage_authors),
            "engagement_mature": mature,
            "engagement_coverage": round(mature / len(items), 3) if items else 0.0,
            "weights_empirical": weights.empirical,
        }
        row.computed_at = datetime.now(timezone.utc)
        written += 1
        day += timedelta(days=1)

    await session.commit()
    return written
