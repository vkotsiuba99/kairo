"""
Воркер сервісу Publishing: періодично опитує лічильники опублікованих дописів і
взаємодії підписників. Результати пишуться подіями в outbox БД Publishing, звідки
їх відправляє ретранслятор сервісу; винагороду рахує Telemetry.

Запускається одним екземпляром (SERVICE=publishing).
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import logging

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_db
from app.publishing.service import collect_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker-metrics] %(message)s")
logger = logging.getLogger("kairo.worker_metrics")


async def run_loop():
    await init_db()  # ідемпотентно: таблиці сервісу, якщо API ще не стартував
    logger.info("Metrics polling every %d s", settings.TELEMETRY_POLL_SECONDS)
    while True:
        try:
            async with AsyncSessionLocal() as session:
                stats = await collect_all(session)
            logger.info(
                "Channels: %d | posts polled: %d | metric events: %d | interactions: %d",
                stats["accounts"], stats["posts_polled"], stats["metric_events"], stats["interactions"],
            )
        except Exception as e:  # БД тимчасово недоступна — пробуємо в наступному циклі
            logger.warning("Metrics loop error: %s", e)
        await asyncio.sleep(settings.TELEMETRY_POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_loop())
