"""
Складання добових ознак і індексу попиту.

    python scripts/build_daily_features.py
    python scripts/build_daily_features.py --horizon 14
    python scripts/build_daily_features.py --weights   # лише показати ваги стадій

Спершу рахує ваги стадій за траєкторіями авторів, потім будує добові рядки.
Поки розмічених згадок мало, ваги беруться запасні — про це друкується прямо,
щоб грубі числа не пішли в роботу як виміряні.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand.envfile import load_credentials  # noqa: E402

load_credentials()

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.demand import entities  # noqa: E402,F401
from app.demand.annotator import REJECTION, STAGES  # noqa: E402
from app.demand.entities import DailyFeature, DemandProduct  # noqa: E402
from app.demand.features import build_daily_features, estimate_stage_weights  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s")


async def _create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _print_weights(w) -> None:
    print(f"Ваги стадій (вікно конверсії {w.horizon_days} днів, "
          f"{'виміряні' if w.empirical else 'ЗАПАСНІ — спостережень ще замало'}):")
    print(f"   {'стадія':<16}{'вага':>8}{'дійшли':>9}{'усього':>9}")
    for stage in (*STAGES, REJECTION):
        reached, total = w.observed.get(stage, (0, 0))
        mark = "" if total else "  ·"
        print(f"   {stage:<16}{w.get(stage):>8.3f}{reached:>9}{total:>9}{mark}")
    if not w.empirical:
        print("\n   · — траєкторій ще нема: щоб виміряти ваги, потрібні автори,")
        print("     які писали про товар двічі. Це зʼявиться після розмітки корпусу.")


async def cmd_run(args) -> None:
    await _create_tables()
    model_name = args.model or settings.KIMI_MODEL_NAME
    async with AsyncSessionLocal() as session:
        weights = await estimate_stage_weights(
            session, horizon_days=args.horizon, model_name=model_name
        )
    _print_weights(weights)
    if args.weights:
        return

    async with AsyncSessionLocal() as session:
        products = (await session.execute(
            select(DemandProduct).where(DemandProduct.in_menu.is_(True))
            .order_by(DemandProduct.display_name)
        )).scalars().all()

    if not products:
        print("\nУ меню немає товарів — спершу зберіть каталог.")
        return

    print(f"\nБудую добові рядки для {len(products)} товарів:\n")
    total_days = 0
    for product in products:
        async with AsyncSessionLocal() as session:
            fresh = await session.get(DemandProduct, product.id)
            days = await build_daily_features(session, fresh, weights, model_name=model_name)
        total_days += days
        print(f"   {product.display_name:<20}{days:>5} діб")

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(DailyFeature)
            .order_by(DailyFeature.day.desc())
            .limit(args.show)
        )).scalars().all()

    print(f"\nЗаписано {total_days} добових рядків.")
    if rows:
        print(f"\nОстанні {len(rows)} рядків:")
        print(f"   {'доба':<12}{'товар':<16}{'згадок':>8}{'авторів':>9}"
              f"{'індекс':>9}{'дублі':>8}{'реакції':>9}")
        async with AsyncSessionLocal() as session:
            names = {
                p.id: p.display_name for p in (await session.execute(select(DemandProduct))).scalars().all()
            }
        for r in rows:
            extras = r.extras or {}
            print(f"   {str(r.day):<12}{names.get(r.product_id, '?')[:15]:<16}"
                  f"{r.mention_count:>8}{r.unique_authors:>9}"
                  f"{extras.get('demand_index', 0):>9.2f}{r.duplicate_ratio:>8.2f}"
                  f"{r.engagement_sum:>9}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Добові ознаки й індекс попиту")
    parser.add_argument("--horizon", type=int, default=30,
                        help="вікно конверсії для ваг стадій, днів")
    parser.add_argument("--model", default=None, help="яка розмітка береться")
    parser.add_argument("--weights", action="store_true", help="лише показати ваги")
    parser.add_argument("--show", type=int, default=12, help="скільки рядків показати")
    args = parser.parse_args()
    asyncio.run(cmd_run(args))


if __name__ == "__main__":
    main()
