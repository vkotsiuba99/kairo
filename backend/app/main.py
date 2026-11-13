import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.init_db import init_db
from app.scheduler.runner import start_scheduler
from app.api.router import api_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kairo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Kairo database & seeding default platforms...")
    await init_db()

    # Create storage directories
    os.makedirs(settings.DATA_ARCHIVE_PATH, exist_ok=True)
    os.makedirs(settings.DATA_MEDIA_PATH, exist_ok=True)

    # Вбудований планувальник — для запуску одним процесом; у docker-compose
    # публікує окремий сервіс publisher (його можна масштабувати).
    scheduler = start_scheduler() if settings.RUN_SCHEDULER_IN_API else None
    logger.info("Kairo backend services initialized.")
    yield
    if scheduler:
        scheduler.shutdown()
    logger.info("Kairo backend shutdown complete.")


app = FastAPI(
    title="Kairo — AI Social Media Content Generation System",
    description=(
        "Research & Production Backend for automated social media generation, "
        "factorized contextual multi-armed bandit decision engine, "
        "and offline audience simulation."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
)

# Enable CORS for frontend dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Mount media directory for generated images/videos
os.makedirs(settings.DATA_MEDIA_PATH, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.DATA_MEDIA_PATH), name="media")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "llm_model": settings.LLM_MODEL_NAME,
        "image_model": settings.IMAGE_MODEL_NAME,
    }
