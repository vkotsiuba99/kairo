"""
Глибокий добір історії по брендах, що пройшли смугу.

    python scripts/run_brand_backfill.py --days 180
    python scripts/run_brand_backfill.py --days 180 --only b_fujifilm,b_nikon
    python scripts/run_brand_backfill.py --report

Реєструє бренди в БД (з паспортом заміру), потім вичерпує історію кожного
до цільової дати й пише згадки пачками — двісті тисяч дописів у пам'ять
не поміщаються.
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand.envfile import load_credentials  # noqa: E402

load_credentials()

from sqlalchemy import func, select  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.demand import entities  # noqa: E402,F401
from app.demand.brands import BAND_MAX, BAND_MIN, BRANDS  # noqa: E402
from app.demand.deep_backfill import DeepBackfill  # noqa: E402
from app.demand.entities import DemandProduct, SocialMention  # noqa: E402
from app.demand.passport import PassportMeter  # noqa: E402
from app.demand.store import ensure_products, store_mentions  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s")


async def _create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def cmd_run(args) -> None:
    await _create_tables()
    handle, password = os.getenv("BLUESKY_HANDLE"), os.getenv("BLUESKY_APP_PASSWORD")
    if not handle or not password:
        print("BLUESKY_HANDLE / BLUESKY_APP_PASSWORD не задані")
        return

    only = {c.strip() for c in args.only.split(",")} if args.only else None
    pool = [b for b in BRANDS if not only or b.code in only]

    # Смугу перевіряємо щоразу: обсяг бренду змінюється, і те, що вчора було
    # придатним, сьогодні могло вилетіти вгору після гучного анонсу.
    print(f"Заміряю {len(pool)} брендів, щоб відібрати смугу {BAND_MIN:.0f}–{BAND_MAX:.0f}/добу…")
    meter = PassportMeter(handle, password, os.getenv("BLUESKY_SERVICE", "https://bsky.social"))
    passports = await meter.measure_all(pool, pause_seconds=0.35)

    chosen = [
        b for b in pool
        if (pp := passports.get(b.code)) is not None
        and BAND_MIN <= pp.posts_per_day <= BAND_MAX
        and pp.verdict in ("ok", "thin")
    ]
    if not chosen:
        print("Жоден бренд не потрапив у смугу.")
        return
    print(f"У смузі: {len(chosen)} — {', '.join(b.name for b in chosen)}\n")

    specs = [b.to_spec() for b in chosen]
    async with AsyncSessionLocal() as session:
        product_ids = await ensure_products(session, specs)
        rows = {
            p.code: p for p in (await session.execute(
                select(DemandProduct).where(DemandProduct.code.in_(list(product_ids)))
            )).scalars().all()
        }
        for b in chosen:
            pp, row = passports[b.code], rows.get(b.code)
            if row is None:
                continue
            row.posts_per_day = pp.posts_per_day
            row.unique_author_ratio = pp.unique_author_ratio
            row.duplicate_ratio = pp.duplicate_ratio
            row.promo_ratio = pp.promo_ratio
            row.sample_size = pp.sample_size
            row.verdict = pp.verdict
            row.in_menu = True
            row.measured_at = pp.measured_at
        await session.commit()

    written = {"n": 0}

    class StorageDown(RuntimeError):
        """БД відвалилась посеред добору."""

    async def on_batch(records):
        # Раніше виняток тут гасив увесь прогін мовчки: скрипт завершувався
        # кодом 0, зробивши один бренд із п'ятнадцяти. Тепер падаємо голосно —
        # повторний запуск безпечний, бо вставка дедуплікується за
        # (platform, external_id) і вже зібране не задвоїться.
        for attempt in range(3):
            try:
                async with AsyncSessionLocal() as session:
                    written["n"] += await store_mentions(session, records, product_ids)
                return
            except Exception as exc:
                if attempt == 2:
                    raise StorageDown(
                        f"База не приймає запис ({type(exc).__name__}: {str(exc)[:120]}). "
                        f"Записано до збою: {written['n']} згадок. "
                        f"Підніміть БД і запустіть скрипт ще раз — дублів не буде."
                    ) from exc
                await asyncio.sleep(5 * (attempt + 1))

    started = datetime.now(timezone.utc)

    def done(stat):
        mins = (datetime.now(timezone.utc) - started).total_seconds() / 60
        print(f"  {stat.code:<18} сторінок {stat.pages:>4}  сирих {stat.raw_posts:>6}"
              f"  наших {stat.kept:>6}  глибина {stat.depth_days:>6.1f} дн"
              f"  помилок {stat.errors:>2}  [{stat.stopped_by}]  ({mins:.0f} хв)", flush=True)

    print(f"Добір на {args.days} днів, ліміт {args.pages} сторінок на бренд:\n")
    engine_bf = DeepBackfill(handle, password, os.getenv("BLUESKY_SERVICE", "https://bsky.social"))
    try:
        stats = await engine_bf.run_all(specs, on_batch, target_days=args.days,
                                        max_pages=args.pages, on_done=done)
    except StorageDown as exc:
        print(f"\nПЕРЕРВАНО: {exc}")
        raise SystemExit(2)

    mins = (datetime.now(timezone.utc) - started).total_seconds() / 60
    print(f"\n{'—' * 70}")
    print(f"Готово за {mins:.0f} хв. Сторінок {sum(s.pages for s in stats)}, "
          f"сирих дописів {sum(s.raw_posts for s in stats)}, "
          f"записано нових згадок {written['n']}")
    reached = [s for s in stats if s.depth_days >= args.days * 0.9]
    print(f"Цільової глибини досягли {len(reached)} з {len(stats)} брендів")


async def cmd_report(args) -> None:
    await _create_tables()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                DemandProduct.display_name,
                func.count(SocialMention.id),
                func.count(func.distinct(SocialMention.author_id)),
                func.min(SocialMention.posted_at),
                func.max(SocialMention.posted_at),
            )
            .join(SocialMention, SocialMention.product_id == DemandProduct.id)
            .where(DemandProduct.code.like("b\\_%", escape="\\"))
            .group_by(DemandProduct.display_name)
            .order_by(func.count(SocialMention.id).desc())
        )).all()
    if not rows:
        print("По брендах ще нічого не зібрано.")
        return
    print(f"{'бренд':<20}{'згадок':>9}{'авторів':>9}{'глибина':>10}   період")
    for name, cnt, authors, lo, hi in rows:
        depth = (hi - lo).days if lo and hi else 0
        print(f"{name:<20}{cnt:>9}{authors:>9}{depth:>8} дн   {str(lo)[:10]} .. {str(hi)[:10]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Глибокий добір історії по брендах")
    parser.add_argument("--days", type=int, default=180, help="цільова глибина в днях")
    parser.add_argument("--pages", type=int, default=500, help="ліміт сторінок на бренд")
    parser.add_argument("--only", default=None, help="список кодів через кому")
    parser.add_argument("--report", action="store_true", help="показати зібране")
    args = parser.parse_args()
    asyncio.run((cmd_report if args.report else cmd_run)(args))


if __name__ == "__main__":
    main()
