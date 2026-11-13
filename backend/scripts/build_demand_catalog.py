"""
Побудова меню: заміряє кандидатів і записує в БД тих, хто пройшов.

    python scripts/build_demand_catalog.py            # заміряти все
    python scripts/build_demand_catalog.py --category photo
    python scripts/build_demand_catalog.py --report   # показати збережене меню

Запускається разово (і потім за потреби перезапускається). У рантаймі
нічого не міряється: інтерфейс просто читає готову таблицю.
"""
import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand.envfile import load_credentials  # noqa: E402

load_credentials()

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.demand import entities  # noqa: E402,F401
from app.demand.catalog import CANDIDATES, CATEGORIES  # noqa: E402
from app.demand.entities import DemandProduct  # noqa: E402
from app.demand.passport import PassportMeter  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s")

VERDICT_LABEL = {
    "ok": "приймаємо",
    "thin": "тонкий сигнал",
    "no_signal": "немає сигналу",
    "astroturf": "накрутка",
    "promo_heavy": "реклама",
}


async def _create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def cmd_build(args) -> None:
    await _create_tables()
    cands = [c for c in CANDIDATES if not args.category or c.category == args.category]
    if not cands:
        print(f"Категорію «{args.category}» не знайдено. Доступні: {', '.join(CATEGORIES)}")
        return

    handle, password = os.getenv("BLUESKY_HANDLE"), os.getenv("BLUESKY_APP_PASSWORD")
    if not handle or not password:
        print("BLUESKY_HANDLE / BLUESKY_APP_PASSWORD не задані")
        return

    print(f"Заміряю {len(cands)} кандидатів…\n")

    def progress(i, total, pp):
        mark = "+" if pp.in_menu else "-"
        noise = ""
        if pp.raw_hits and pp.sample_size < pp.raw_hits:
            noise = f"  (з {pp.raw_hits} відсіяно {pp.raw_hits - pp.sample_size})"
        print(f"  [{i:>3}/{total}] {mark} {pp.name:<28}{pp.posts_per_day:>8.1f}/добу"
              f"  авт.{pp.unique_author_ratio:>5.2f}  "
              f"{VERDICT_LABEL.get(pp.verdict, pp.verdict)}{noise}")

    meter = PassportMeter(handle, password, os.getenv("BLUESKY_SERVICE", "https://bsky.social"))
    passports = await meter.measure_all(cands, on_progress=progress)

    async with AsyncSessionLocal() as session:
        existing = {
            p.code: p for p in (await session.execute(
                select(DemandProduct).where(DemandProduct.code.in_(list(passports)))
            )).scalars().all()
        }
        for cand in cands:
            pp = passports.get(cand.code)
            if pp is None:
                continue
            spec = cand.to_spec()
            lexicon = {
                "include": list(spec.include),
                "exclude": list(spec.exclude),
                "ambiguous": {k: list(v) for k, v in spec.ambiguous.items()},
                "promo_hints": list(spec.promo_hints),
                "sku": {},
            }
            row = existing.get(cand.code)
            if row is None:
                row = DemandProduct(code=cand.code, display_name=cand.name, category=cand.category)
                session.add(row)
            row.display_name = cand.name
            row.category = cand.category
            row.lexicon = lexicon
            row.posts_per_day = pp.posts_per_day
            row.unique_author_ratio = pp.unique_author_ratio
            row.duplicate_ratio = pp.duplicate_ratio
            row.promo_ratio = pp.promo_ratio
            row.history_days = pp.history_days
            row.sample_size = pp.sample_size
            row.verdict = pp.verdict
            row.in_menu = pp.in_menu
            row.measured_at = pp.measured_at
        await session.commit()

    tally = Counter(p.verdict for p in passports.values())
    in_menu = sum(1 for p in passports.values() if p.in_menu)
    print(f"\n{'—' * 62}")
    print(f"Заміряно {len(passports)}, у меню потрапило {in_menu}")
    for verdict, n in tally.most_common():
        print(f"   {VERDICT_LABEL.get(verdict, verdict):<18}{n:>4}")


async def cmd_report(args) -> None:
    await _create_tables()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(DemandProduct).order_by(
                DemandProduct.category, DemandProduct.posts_per_day.desc()
            )
        )).scalars().all()
    if not rows:
        print("Каталог порожній — запустіть без --report.")
        return
    current = None
    shown = 0
    for r in rows:
        if not r.in_menu and not args.all:
            continue
        if r.category != current:
            current = r.category
            print(f"\n{CATEGORIES.get(current, current)}")
        flag = "·" if r.verdict == "ok" else "~"
        print(f"  {flag} {r.display_name:<32}{(r.posts_per_day or 0):>8.1f}/добу"
              f"  авт.{(r.unique_author_ratio or 0):>5.2f}"
              f"  іст.{(r.history_days or 0):>6.0f}д"
              f"  {VERDICT_LABEL.get(r.verdict, r.verdict)}")
        shown += 1
    print(f"\nВсього показано {shown}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Побудова меню товарів за заміром сигналу")
    parser.add_argument("--category", default=None, help="заміряти лише одну категорію")
    parser.add_argument("--report", action="store_true", help="показати збережене меню")
    parser.add_argument("--all", action="store_true", help="у звіті показати й відсіяних")
    args = parser.parse_args()
    asyncio.run((cmd_report if args.report else cmd_build)(args))


if __name__ == "__main__":
    main()
