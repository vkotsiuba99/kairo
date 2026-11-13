"""
API підсистеми прогнозування попиту.

Меню читається з таблиці, яку наповнив разовий замір
(`scripts/build_demand_catalog.py`). У рантаймі нічого не вимірюється
і нічого не вигадується: інтерфейс показує рівно те, що пройшло пороги.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.demand.catalog import CATEGORIES
from app.demand.entities import DemandProduct, SocialMention

router = APIRouter(prefix="/demand", tags=["Demand Forecasting"])

VERDICT_LABEL = {
    "ok": "signal confirmed",
    "thin": "thin signal",
    "no_signal": "no signal",
    "astroturf": "astroturfing",
    "promo_heavy": "mostly ads",
}


def _passport(p: DemandProduct, collected: int = 0) -> Dict[str, Any]:
    return {
        "code": p.code,
        "name": p.display_name,
        "category": p.category,
        "category_title": CATEGORIES.get(p.category, p.category),
        "posts_per_day": p.posts_per_day or 0.0,
        "unique_author_ratio": p.unique_author_ratio or 0.0,
        "duplicate_ratio": p.duplicate_ratio or 0.0,
        "promo_ratio": p.promo_ratio or 0.0,
        "history_days": p.history_days or 0.0,
        "sample_size": p.sample_size or 0,
        "verdict": p.verdict or "no_signal",
        "verdict_label": VERDICT_LABEL.get(p.verdict or "", p.verdict or ""),
        "in_menu": bool(p.in_menu),
        "measured_at": p.measured_at.isoformat() if p.measured_at else None,
        "mentions_collected": collected,
    }


async def _collected_map(db: AsyncSession) -> Dict[int, int]:
    rows = (await db.execute(
        select(SocialMention.product_id, func.count(SocialMention.id))
        .group_by(SocialMention.product_id)
    )).all()
    return {pid: n for pid, n in rows}


@router.get("/catalog")
async def get_catalog(
    include_rejected: bool = Query(False, description="include rejected products"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Меню: категорії та товари, що пройшли замір сигналу."""
    stmt = select(DemandProduct).order_by(
        DemandProduct.category, DemandProduct.posts_per_day.desc().nullslast()
    )
    if not include_rejected:
        stmt = stmt.where(DemandProduct.in_menu.is_(True))
    products = (await db.execute(stmt)).scalars().all()
    collected = await _collected_map(db)

    payload = [_passport(p, collected.get(p.id, 0)) for p in products]
    counts: Dict[str, int] = {}
    for item in payload:
        counts[item["category"]] = counts.get(item["category"], 0) + 1

    categories = [
        {"code": code, "title": title, "count": counts.get(code, 0)}
        for code, title in CATEGORIES.items()
        if counts.get(code, 0) > 0
    ]
    return {
        "categories": categories,
        "products": payload,
        "total": len(payload),
    }


@router.get("/products/{code}")
async def get_product(code: str, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    product = (await db.execute(
        select(DemandProduct).where(DemandProduct.code == code)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    rows = (await db.execute(
        select(
            SocialMention.platform,
            SocialMention.source,
            func.count(SocialMention.id),
            func.count(func.distinct(SocialMention.author_id)),
            func.sum(case((SocialMention.is_bridged.is_(True), 1), else_=0)),
            func.sum(case((SocialMention.looks_promo.is_(True), 1), else_=0)),
            func.min(SocialMention.posted_at),
            func.max(SocialMention.posted_at),
        )
        .where(SocialMention.product_id == product.id)
        .group_by(SocialMention.platform, SocialMention.source)
    )).all()

    corpus = [
        {
            "platform": platform,
            "source": source,
            "mentions": cnt,
            "authors": authors,
            "bridged": int(bridged or 0),
            "promo": int(promo or 0),
            "from": lo.isoformat() if lo else None,
            "to": hi.isoformat() if hi else None,
        }
        for platform, source, cnt, authors, bridged, promo, lo, hi in rows
    ]
    total = sum(c["mentions"] for c in corpus)
    return {"passport": _passport(product, total), "corpus": corpus}


@router.get("/products/{code}/series")
async def get_series(
    code: str,
    days: int = Query(90, ge=7, le=730),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Добовий ряд згадок — вхід майбутньої моделі й водночас те, що малюємо."""
    product = (await db.execute(
        select(DemandProduct).where(DemandProduct.code == code)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = func.date(SocialMention.posted_at)
    rows = (await db.execute(
        select(
            day.label("day"),
            func.count(SocialMention.id),
            func.count(func.distinct(SocialMention.author_id)),
            func.sum(SocialMention.like_count + SocialMention.repost_count),
        )
        .where(SocialMention.product_id == product.id, SocialMention.posted_at >= since)
        .group_by(day)
        .order_by(day)
    )).all()

    def _iso(value) -> str:
        if isinstance(value, (date, datetime)):
            return value.isoformat()[:10]
        return str(value)[:10]

    points = [
        {
            "day": _iso(d),
            "mentions": int(cnt or 0),
            "authors": int(authors or 0),
            "engagement": int(eng or 0),
        }
        for d, cnt, authors, eng in rows
    ]
    return {"code": code, "name": product.display_name, "days": days, "points": points}
