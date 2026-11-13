import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import logging

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.telemetry.collector import collect_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker-telemetry] %(message)s")
logger = logging.getLogger("kairo.worker_telemetry")

POLL_INTERVAL_SECONDS = settings.TELEMETRY_POLL_SECONDS


async def run_telemetry_loop():
    logger.info("Starting the telemetry loop (every %d s)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            async with AsyncSessionLocal() as session:
                stats = await collect_all(session)
            if stats["events"] or stats["profiles_new"]:
                logger.info(
                    "Channels: %d | posts polled: %d | new events: %d | "
                    "interactions: %d | new profiles: %d",
                    stats["accounts"],
                    stats["posts_polled"],
                    stats["events"],
                    stats["interactions"],
                    stats["profiles_new"],
                )
            else:
                logger.info(
                    "Channels: %d | posts polled: %d | no new events",
                    stats["accounts"],
                    stats["posts_polled"],
                )
        except Exception as e:
            logger.warning("Telemetry loop error: %s", e)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_telemetry_loop())
