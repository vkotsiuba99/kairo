"""
Розподіл стадій воронки за категоріями та брендами.

    python scripts/stage_breakdown.py
    python scripts/stage_breakdown.py --by brand

Головне число тут — частка сигнальних стадій. Саме вона визначає, чи взагалі
можливий добовий ряд: при 3% бренд зі 170 згадками на добу дає близько п'яти
сигнальних авторів на день, і це шум, а не ряд.

`usage` показано окремо не для краси: у фотобрендах це три чверті корпусу
(люди викладають знімки, а не обговорюють покупку), і саме тому цю стадію
прибрано з цілі в ознаки.
"""
import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand.envfile import load_credentials  # noqa: E402

load_credentials()

from sqlalchemy import func, select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.demand.annotator import REJECTION, STAGES  # noqa: E402
from app.demand.catalog import CATEGORIES  # noqa: E402
from app.demand.entities import DemandProduct, MentionAnnotation, SocialMention  # noqa: E402

SIGNAL_STAGES = ("interest", "consideration", "intent", "ordered",
                 "received", "repurchase", REJECTION)


async def main(group_by: str) -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                DemandProduct.display_name,
                DemandProduct.category,
                MentionAnnotation.intent_stage,
                func.count(MentionAnnotation.id),
            )
            .join(SocialMention, SocialMention.product_id == DemandProduct.id)
            .join(MentionAnnotation, MentionAnnotation.mention_id == SocialMention.id)
            .group_by(DemandProduct.display_name, DemandProduct.category,
                      MentionAnnotation.intent_stage)
        )).all()
        scarcity = (await session.execute(
            select(DemandProduct.category, MentionAnnotation.scarcity,
                   func.count(MentionAnnotation.id))
            .join(SocialMention, SocialMention.product_id == DemandProduct.id)
            .join(MentionAnnotation, MentionAnnotation.mention_id == SocialMention.id)
            .where(MentionAnnotation.scarcity != "none")
            .group_by(DemandProduct.category, MentionAnnotation.scarcity)
        )).all()

    if not rows:
        print("Розмічених згадок ще немає.")
        return

    buckets = defaultdict(lambda: defaultdict(int))
    for name, category, stage, n in rows:
        key = name if group_by == "brand" else CATEGORIES.get(category, category)
        buckets[key][stage or "?"] += n

    header = "бренд" if group_by == "brand" else "категорія"
    print(f"{header:<26}{'усього':>8}{'usage':>8}{'none':>7}{'сигнал':>8}"
          f"{'частка сигналу':>16}")
    print("─" * 73)
    grand = defaultdict(int)
    for key, dist in sorted(buckets.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(dist.values())
        signal = sum(dist.get(s, 0) for s in SIGNAL_STAGES)
        for stage, n in dist.items():
            grand[stage] += n
        print(f"{key:<26}{total:>8}{dist.get('usage', 0):>8}{dist.get('none', 0):>7}"
              f"{signal:>8}{signal / total * 100:>15.1f}%")

    total = sum(grand.values())
    signal = sum(grand.get(s, 0) for s in SIGNAL_STAGES)
    print("─" * 73)
    print(f"{'РАЗОМ':<26}{total:>8}{grand.get('usage', 0):>8}{grand.get('none', 0):>7}"
          f"{signal:>8}{signal / total * 100:>15.1f}%")

    print("\nПовний розподіл стадій:")
    for stage in (*STAGES, REJECTION):
        n = grand.get(stage, 0)
        bar = "█" * int(n / max(1, total) * 50)
        mark = " ←" if stage in SIGNAL_STAGES else "  "
        print(f"   {stage:<15}{n:>7}{n / total * 100:>7.1f}%{mark} {bar}")

    if scarcity:
        by_cat = defaultdict(lambda: defaultdict(int))
        for category, sc, n in scarcity:
            by_cat[CATEGORIES.get(category, category)][sc] += n
        print("\nСигнали дефіциту за категоріями:")
        for cat, d in sorted(by_cat.items(), key=lambda kv: -sum(kv[1].values())):
            parts = ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda x: -x[1]))
            print(f"   {cat:<26}{parts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Розподіл стадій воронки")
    parser.add_argument("--by", default="category", choices=("category", "brand"))
    args = parser.parse_args()
    asyncio.run(main(args.by))
