"""
Розмітка згадок мовною моделлю.

    python scripts/run_annotation.py --limit 2000 --backend kimi
    python scripts/run_annotation.py --limit 200 --backend kimi --dry-run
    python scripts/run_annotation.py --report

Вибірка стратифікована за брендом і добою. Це не примха: якщо брати підряд,
вибірку заб'є один гучний бренд в один гучний тиждень, і навчена на ній
модель бачитиме світ його очима. Рівномірність по добах важлива ще й тому,
що ціль — добовий ряд, і кожна доба має бути представлена.

Розмітка платна, тому корпус цілком не розмічається: береться вибірка,
а решту згодом розмічає дешевий класифікатор, навчений на ній.
"""
import argparse
import asyncio
import logging
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand.envfile import load_credentials  # noqa: E402

load_credentials()

from sqlalchemy import func, select  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.demand import entities  # noqa: E402,F401
from app.demand.annotator import MentionAnnotator  # noqa: E402
from app.demand.entities import DemandProduct, MentionAnnotation, SocialMention  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s")


async def _create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _pick_sample(session, limit: int, model_name: str, seed: int,
                       only_codes=None, weeks=None):
    """
    Рівномірно по брендах, усередині бренду — рівномірно по добах.

    `only_codes` звужує вибірку до кількох брендів. Це не оптимізація, а
    виправлення помилки проєктування: дві тисячі згадок, розмазані по
    сімнадцяти брендах і ста вісімдесяти добах, дають 0.65 розміченої
    згадки на бренд на добу — з такого не виходить ані добовий ряд, ані
    тижневий. Для показу технології потрібна глибина по одному бренду,
    а не ширина по всіх.
    """
    already = select(MentionAnnotation.mention_id).where(
        MentionAnnotation.model_name == model_name
    )
    rows_stmt = (
        select(
            SocialMention.id,
            SocialMention.product_id,
            SocialMention.text,
            func.date(SocialMention.posted_at),
            DemandProduct.display_name,
        )
        .join(DemandProduct, DemandProduct.id == SocialMention.product_id)
        .where(SocialMention.id.notin_(already))
    )
    if only_codes:
        rows_stmt = rows_stmt.where(DemandProduct.code.in_(list(only_codes)))
    if weeks:
        # Вікно рахуємо від останньої зібраної згадки, а не від «сьогодні»:
        # корпус має край, і прив'язка до поточної дати мовчки з'їдала б
        # частину вікна, якби збір робився вчора.
        newest = (await session.execute(select(func.max(SocialMention.posted_at)))).scalar_one()
        if newest:
            rows_stmt = rows_stmt.where(
                SocialMention.posted_at >= newest - timedelta(weeks=weeks)
            )
    rows = (await session.execute(rows_stmt)).all()
    if not rows:
        return []

    by_product = defaultdict(lambda: defaultdict(list))
    for mid, pid, text, day, name in rows:
        by_product[(pid, name)][str(day)].append((mid, text))

    rng = random.Random(seed)
    per_product = max(1, limit // len(by_product))
    picked = []
    for key, days in by_product.items():
        per_day = max(1, per_product // max(1, len(days)))
        bucket = []
        for day, items in days.items():
            rng.shuffle(items)
            bucket.extend((key[1], mid, text) for mid, text in items[:per_day])
        rng.shuffle(bucket)
        picked.extend(bucket[:per_product])
    rng.shuffle(picked)
    return picked[:limit]


async def cmd_run(args) -> None:
    await _create_tables()
    annotator = MentionAnnotator(batch_size=args.batch, backend=args.backend)

    async with AsyncSessionLocal() as session:
        only = {c.strip() for c in args.product.split(",")} if args.product else None
        sample = await _pick_sample(session, args.limit, annotator.model_name,
                                    args.seed, only_codes=only, weeks=args.weeks)

    if not sample:
        print("Немає нерозмічених згадок — спершу зберіть корпус.")
        return

    spread = defaultdict(int)
    for name, _mid, _text in sample:
        spread[name] += 1
    print(f"Вибірка: {len(sample)} згадок із {len(spread)} брендів "
          f"(бекенд {args.backend}, модель {annotator.model_name})")
    for name, n in sorted(spread.items(), key=lambda x: -x[1]):
        print(f"   {name:<18}{n:>6}")
    if args.dry_run:
        print("\n--dry-run: викликів не робив.")
        return

    # Пачка збирається в межах одного бренду: у промпті є назва товару, і
    # змішувати різні бренди в одному виклику означало б її загубити.
    batches = defaultdict(list)
    for name, mid, text in sample:
        batches[name].append((mid, text))

    chunks = []
    for name, items in batches.items():
        for i in range(0, len(items), args.batch):
            chunks.append((name, items[i:i + args.batch]))

    started = datetime.now(timezone.utc)
    progress = {"saved": 0, "done": 0}
    # Виклики йдуть паралельно: модель із міркуванням відповідає десятки секунд,
    # і послідовно дві тисячі згадок розмічались би довше, ніж збирався корпус.
    gate = asyncio.Semaphore(args.concurrency)

    async def handle(name, chunk):
        try:
            async with gate:
                anns = await annotator.annotate_batch(name, [t for _m, t in chunk])
        except Exception as exc:
            progress["done"] += 1
            print(f"  пачка {progress['done']:>4}/{len(chunks)}  ЗБІЙ {type(exc).__name__}", flush=True)
            return
        rows = [
            MentionAnnotation(
                mention_id=chunk[a.index][0],
                model_name=annotator.model_name,
                relevant=a.relevant,
                intent_stage=a.stage,
                scarcity=a.scarcity,
                sentiment=a.sentiment,
                post_format=a.post_format,
                is_ad=a.is_ad,
                aspects=[],
            )
            for a in anns if a.index < len(chunk)
        ]
        if rows:
            async with AsyncSessionLocal() as session:
                session.add_all(rows)
                await session.commit()
            progress["saved"] += len(rows)
        progress["done"] += 1
        mins = (datetime.now(timezone.utc) - started).total_seconds() / 60
        print(f"  пачка {progress['done']:>4}/{len(chunks)}  розмічено {progress['saved']:>6}"
              f"  збоїв {annotator.failures:>3}"
              f"  токенів {annotator.prompt_tokens + annotator.completion_tokens:>9,}"
              f"  ({mins:.1f} хв)", flush=True)

    print(f"\nПачок {len(chunks)} по {args.batch}, паралельно {args.concurrency}:\n")
    await asyncio.gather(*(handle(n, c) for n, c in chunks))
    saved = progress["saved"]

    total = annotator.prompt_tokens + annotator.completion_tokens
    per_mention = total / max(1, saved)
    print(f"\n{'—' * 70}")
    print(f"Розмічено {saved} згадок за {annotator.calls} викликів, збоїв {annotator.failures}")
    print(f"Токенів: промпт {annotator.prompt_tokens:,}, відповідь {annotator.completion_tokens:,}, "
          f"разом {total:,} ({per_mention:.0f} на згадку)")


async def cmd_report(args) -> None:
    await _create_tables()
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count(MentionAnnotation.id)))).scalar_one()
        by_stage = (await session.execute(
            select(MentionAnnotation.intent_stage, func.count(MentionAnnotation.id))
            .group_by(MentionAnnotation.intent_stage)
            .order_by(func.count(MentionAnnotation.id).desc())
        )).all()
        by_scarcity = (await session.execute(
            select(MentionAnnotation.scarcity, func.count(MentionAnnotation.id))
            .where(MentionAnnotation.scarcity != "none")
            .group_by(MentionAnnotation.scarcity)
            .order_by(func.count(MentionAnnotation.id).desc())
        )).all()
    if not total:
        print("Розмічених згадок ще немає.")
        return
    print(f"Розмічено всього: {total}\n\nСтадії воронки:")
    for stage, n in by_stage:
        print(f"   {stage or '—':<16}{n:>7}{n / total * 100:>7.1f}%")
    if by_scarcity:
        print("\nСигнали дефіциту:")
        for sc, n in by_scarcity:
            print(f"   {sc:<16}{n:>7}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Розмітка згадок мовною моделлю")
    parser.add_argument("--limit", type=int, default=1000, help="розмір вибірки")
    parser.add_argument("--batch", type=int, default=10, help="згадок на виклик")
    parser.add_argument("--backend", default="kimi", choices=("kimi", "local"))
    parser.add_argument("--seed", type=int, default=42, help="зерно вибірки — для відтворюваності")
    parser.add_argument("--weeks", type=int, default=None,
                        help="обмежити останніми N тижнями від краю корпусу")
    parser.add_argument("--product", default=None,
                        help="коди брендів через кому — звузити розмітку")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="паралельних викликів; вище 5 Moonshot починає ставити в чергу")
    parser.add_argument("--dry-run", action="store_true", help="показати вибірку без викликів")
    parser.add_argument("--report", action="store_true", help="що вже розмічено")
    args = parser.parse_args()
    asyncio.run((cmd_report if args.report else cmd_run)(args))


if __name__ == "__main__":
    main()
