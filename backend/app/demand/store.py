"""
Запис згадок із дедуплікацією.

Колектори працюють паралельно і навмисно перетинаються: jetstream ловить
допис у момент створення, пошук добирає те саме за годину вже з лічильниками.
Тому ключ (platform, external_id) унікальний, а повторна вставка — не помилка,
а штатний шлях.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand.entities import DemandProduct, SocialMention
from app.demand.matcher import text_hash
from app.demand.products import ProductSpec

logger = logging.getLogger("kairo.demand.store")


@dataclass
class MentionRecord:
    platform: str
    external_id: str
    source: str
    author_id: str
    posted_at: datetime
    text: str
    product_code: str
    matched_terms: List[str] = field(default_factory=list)
    sku_codes: List[str] = field(default_factory=list)
    looks_promo: bool = False
    is_bridged: bool = False
    author_handle: Optional[str] = None
    lang: Optional[str] = None
    is_reply: bool = False
    reply_to_external_id: Optional[str] = None
    like_count: int = 0
    repost_count: int = 0
    reply_count: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


async def ensure_products(session: AsyncSession, specs: Sequence[ProductSpec]) -> Dict[str, int]:
    """Створює записи товарів за потреби, повертає code -> id."""
    codes = [s.code for s in specs]
    rows = (await session.execute(
        select(DemandProduct).where(DemandProduct.code.in_(codes))
    )).scalars().all()
    existing = {r.code: r for r in rows}

    for spec in specs:
        lexicon = {
            "include": list(spec.include),
            "exclude": list(spec.exclude),
            "ambiguous": {k: list(v) for k, v in spec.ambiguous.items()},
            "promo_hints": list(spec.promo_hints),
            "sku": {k: list(v) for k, v in spec.sku.items()},
        }
        if spec.code in existing:
            # Лексикон еволюціонує — тримаємо в БД актуальну версію,
            # щоб було видно, з якими правилами зібрано корпус.
            existing[spec.code].lexicon = lexicon
            continue
        session.add(DemandProduct(
            code=spec.code,
            display_name=spec.display_name,
            category=spec.category,
            lexicon=lexicon,
        ))
    await session.commit()

    rows = (await session.execute(
        select(DemandProduct).where(DemandProduct.code.in_(codes))
    )).scalars().all()
    return {r.code: r.id for r in rows}


async def store_mentions(
    session: AsyncSession,
    records: Iterable[MentionRecord],
    product_ids: Dict[str, int],
) -> int:
    """Вставляє нові згадки, мовчки пропускаючи вже відомі. Повертає кількість нових."""
    records = list(records)
    if not records:
        return 0

    # Дедуплікація в межах товару, а не глобально: той самий допис може
    # належати кільком товарам одночасно.
    by_key: Dict[tuple, List[str]] = {}
    for r in records:
        pid = product_ids.get(r.product_code)
        if pid is None:
            continue
        by_key.setdefault((pid, r.platform), []).append(r.external_id)

    known: set = set()
    for (pid, platform), ext_ids in by_key.items():
        # Пачками, щоб не впертись у ліміт числа параметрів SQLite.
        for i in range(0, len(ext_ids), 400):
            chunk = ext_ids[i:i + 400]
            rows = (await session.execute(
                select(SocialMention.product_id, SocialMention.platform,
                       SocialMention.external_id).where(
                    SocialMention.product_id == pid,
                    SocialMention.platform == platform,
                    SocialMention.external_id.in_(chunk),
                )
            )).all()
            known.update(rows)

    inserted = 0
    seen_in_batch: set = set()
    for r in records:
        pid = product_ids.get(r.product_code)
        if pid is None:
            logger.warning("Unknown product %s — mention skipped", r.product_code)
            continue
        key = (pid, r.platform, r.external_id)
        if key in known or key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        session.add(SocialMention(
            product_id=pid,
            platform=r.platform,
            external_id=r.external_id,
            source=r.source,
            author_id=r.author_id,
            author_handle=r.author_handle,
            posted_at=r.posted_at,
            text=r.text,
            lang=r.lang,
            is_reply=r.is_reply,
            reply_to_external_id=r.reply_to_external_id,
            like_count=r.like_count,
            repost_count=r.repost_count,
            reply_count=r.reply_count,
            matched_terms=r.matched_terms,
            sku_codes=r.sku_codes,
            looks_promo=r.looks_promo,
            is_bridged=r.is_bridged,
            text_hash=text_hash(r.text),
            raw=r.raw,
        ))
        inserted += 1

    if inserted:
        await session.commit()
    return inserted
