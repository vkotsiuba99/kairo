"""
Точка входу сервісу Kairo.

Один образ, шість сервісів: SERVICE визначає, які маршрути, таблиці й фонові
цикли піднімає процес. SERVICE=all запускає всі сервіси в одному процесі
(локальна розробка й тести) — з тими самими межами: окремі сесії до своїх
таблиць, REST між сервісами через ASGI і шина подій у пам'яті.
"""

import asyncio
import importlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import events
from app.core.clients import ServiceError
from app.core.config import settings
from app.core.init_db import init_db
from app.services import SERVICE_ROUTERS, local_services

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kairo")

# обробники подій реєструються під час імпорту модулів сервісів
importlib.import_module("app.handlers")


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = local_services()
    logger.info("Starting Kairo services: %s", ", ".join(services))
    await init_db()
    os.makedirs(settings.DATA_ARCHIVE_PATH, exist_ok=True)
    os.makedirs(settings.DATA_MEDIA_PATH, exist_ok=True)

    bus = events.make_bus(set(services))
    connecting = asyncio.create_task(bus.start())  # брокер не блокує старт сервісу
    stop = asyncio.Event()
    tasks = []
    if services != ["media"]:  # у Media немає БД, отже й outbox
        tasks.append(asyncio.create_task(events.run_relay(bus, stop)))

    # Диспетчер черги — у сервісі Scheduler; репліки безпечні завдяки атомарному захопленню
    scheduler = None
    if "scheduler" in services and settings.RUN_SCHEDULER_IN_API:
        from app.scheduler.runner import start_scheduler

        scheduler = start_scheduler()

    yield

    stop.set()
    for task in tasks:
        await task
    connecting.cancel()
    if scheduler:
        scheduler.shutdown()
    await bus.close()
    logger.info("Kairo shutdown complete.")


app = FastAPI(
    title="Kairo — adaptive social media content generation",
    description="Microservices: content, scheduler, publishing, telemetry, media, trends.",
    version=settings.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    # помилка іншого сервісу доходить до клієнта з тим самим статусом
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


api_router = APIRouter()
for service in local_services():
    for module in SERVICE_ROUTERS[service]:
        api_router.include_router(importlib.import_module(f"app.api.v1.{module}").router)
app.include_router(api_router, prefix=settings.API_V1_STR)

if "media" in local_services():
    os.makedirs(settings.DATA_MEDIA_PATH, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.DATA_MEDIA_PATH), name="media")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.SERVICE,
        "services": local_services(),
        "version": settings.VERSION,
    }
