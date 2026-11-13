"""
Точка входу збирача згадок.

    python scripts/run_demand_collector.py backfill    # історія через пошук
    python scripts/run_demand_collector.py mastodon    # теги + пошук
    python scripts/run_demand_collector.py stream      # реальний час, безкінечно
    python scripts/run_demand_collector.py stream --seconds 60
    python scripts/run_demand_collector.py stats

`backfill` і `mastodon` варто прогнати один раз, щоб корпус не починався з нуля,
а `stream` тримати запущеним: ретроспективи стрічки не існує, і кожна година
без збирача — це година, якої в даних не буде ніколи.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand.envfile import load_credentials  # noqa: E402

load_credentials()

from sqlalchemy import case, func, select  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.demand import entities  # noqa: E402,F401  (реєстрація таблиць у metadata)
from app.demand.collectors.bluesky_jetstream import JetstreamCollector  # noqa: E402
from app.demand.collectors.bluesky_search import BlueskySearchCollector  # noqa: E402
from app.demand.collectors.mastodon import MastodonCollector  # noqa: E402
from app.demand.entities import DemandProduct, SocialMention  # noqa: E402
from app.demand.matcher import LexiconMatcher  # noqa: E402
from app.demand.products import ACTIVE_PRODUCTS  # noqa: E402
from app.demand.store import ensure_products, store_mentions  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kairo.demand.runner")


async def _create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _prepare():
    await _create_tables()
    async with AsyncSessionLocal() as session:
        product_ids = await ensure_products(session, ACTIVE_PRODUCTS)
    return product_ids, LexiconMatcher(ACTIVE_PRODUCTS)


async def cmd_backfill(args) -> None:
    product_ids, matcher = await _prepare()
    handle = os.getenv("BLUESKY_HANDLE")
    app_password = os.getenv("BLUESKY_APP_PASSWORD")
    if not handle or not app_password:
        logger.error("BLUESKY_HANDLE / BLUESKY_APP_PASSWORD не задані")
        return
    collector = BlueskySearchCollector(
        handle, app_password, matcher,
        service=os.getenv("BLUESKY_SERVICE", "https://bsky.social"),
    )
    records = await collector.backfill(list(ACTIVE_PRODUCTS), max_pages_per_query=args.pages)
    async with AsyncSessionLocal() as session:
        inserted = await store_mentions(session, records, product_ids)
    logger.info("Добір завершено: знайдено %d, нових %d", len(records), inserted)


async def cmd_mastodon(args) -> None:
    product_ids, matcher = await _prepare()
    instance = os.getenv("MASTODON_INSTANCE_URL", "https://mastodon.social")
    collector = MastodonCollector(instance, matcher, os.getenv("MASTODON_ACCESS_TOKEN"))
    records = await collector.poll_tags(list(ACTIVE_PRODUCTS), pages_per_tag=args.pages)
    records += await collector.search(list(ACTIVE_PRODUCTS))
    async with AsyncSessionLocal() as session:
        inserted = await store_mentions(session, records, product_ids)
    logger.info("Mastodon: знайдено %d, нових %d", len(records), inserted)


async def cmd_stream(args) -> None:
    product_ids, matcher = await _prepare()
    collector = JetstreamCollector(matcher, cursor_path=args.cursor)

    async def on_batch(batch):
        if not batch:
            return
        async with AsyncSessionLocal() as session:
            inserted = await store_mentions(session, batch, product_ids)
        logger.info(
            "Пачка: %d згадок, нових %d | переглянуто дописів %d, збіглось %d",
            len(batch), inserted, collector.seen_events, collector.matched_events,
        )

    logger.info("Слухаю Jetstream%s", f" {args.seconds}с" if args.seconds else " (Ctrl+C для зупинки)")
    try:
        await collector.run(on_batch, stop_after=args.seconds)
    except KeyboardInterrupt:
        logger.info("Зупинено вручну")


async def cmd_stats(args) -> None:
    await _create_tables()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                DemandProduct.display_name,
                SocialMention.platform,
                SocialMention.source,
                func.count(SocialMention.id),
                func.count(func.distinct(SocialMention.author_id)),
                func.sum(case((SocialMention.is_bridged.is_(True), 1), else_=0)),
                func.sum(case((SocialMention.looks_promo.is_(True), 1), else_=0)),
                func.min(SocialMention.posted_at),
                func.max(SocialMention.posted_at),
            )
            .join(SocialMention, SocialMention.product_id == DemandProduct.id)
            .group_by(DemandProduct.display_name, SocialMention.platform, SocialMention.source)
        )).all()
    if not rows:
        print("Корпус порожній — запустіть backfill або stream.")
        return
    header = (f"{'товар':<22}{'майданчик':<11}{'джерело':<10}{'згадок':>8}"
              f"{'авторів':>9}{'мости':>7}{'промо':>7}   період")
    print(header)
    for name, platform, source, cnt, authors, bridged, promo, lo, hi in rows:
        span = f"{str(lo)[:10]} .. {str(hi)[:10]}" if lo else "-"
        print(f"{name:<22}{platform:<11}{source:<10}{cnt:>8}{authors:>9}"
              f"{int(bridged or 0):>7}{int(promo or 0):>7}   {span}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Збирач згадок для прогнозування попиту")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backfill", help="історія Bluesky через авторизований пошук")
    p.add_argument("--pages", type=int, default=10, help="сторінок по 100 дописів на запит")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("mastodon", help="теги та пошук Mastodon")
    p.add_argument("--pages", type=int, default=3)
    p.set_defaults(func=cmd_mastodon)

    p = sub.add_parser("stream", help="реальний час через Jetstream")
    p.add_argument("--seconds", type=float, default=None, help="зупинитись через N секунд")
    p.add_argument("--cursor", default="data/demand_jetstream.cursor")
    p.set_defaults(func=cmd_stream)

    p = sub.add_parser("stats", help="що вже зібрано")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
