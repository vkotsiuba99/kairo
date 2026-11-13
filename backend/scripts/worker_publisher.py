"""Окремий воркер-публікатор: забирає з черги (таблиця posts) дописи, час яких настав."""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import logging

from app.core.config import settings
from app.core.init_db import init_db
from app.scheduler.runner import publish_due_posts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [publisher] %(message)s")
logger = logging.getLogger("kairo.publisher")


async def main():
    await init_db()  # ідемпотентно: таблиці й нові колонки, якщо API ще не стартував
    logger.info("Publisher started, interval %d s", settings.SCHEDULER_INTERVAL_SECONDS)
    while True:
        try:
            await publish_due_posts()
        except Exception as e:  # БД тимчасово недоступна — пробуємо в наступному циклі
            logger.warning("Publisher cycle failed: %s", e)
        await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
