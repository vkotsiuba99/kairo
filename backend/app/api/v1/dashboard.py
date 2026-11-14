"""
Дашборд (сервіс Telemetry): зведення показників усіх сервісів.

Кожен сервіс віддає лише свої дані (Content — експерименти й матеріали,
Scheduler — черга, Telemetry — події), запити йдуть паралельно. Якщо сервіс
недоступний, дашборд показує решту й позначає його в стані системи.
"""

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients import ServiceError, call
from app.core.config import settings
from app.core.database import get_db
from app.models.entities import TelemetryEvent
from app.schemas.api import DashboardMetricsResponse

router = APIRouter(prefix="/dashboard", tags=["Dashboard Metrics"])


async def _safe(coro, default):
    try:
        return await coro, True
    except ServiceError:
        return default, False


@router.get("/metrics", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(experiment_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    params = {"experiment_id": experiment_id} if experiment_id else {}
    (content, content_ok), (queue, scheduler_ok) = await asyncio.gather(
        _safe(call("content", "GET", "/content/internal/overview", params=params), {}),
        _safe(call("scheduler", "GET", "/schedule/internal/stats"), {}),
    )
    total_telemetry = (await db.execute(select(func.count(TelemetryEvent.id)))).scalar() or 0

    llm = content.get("llm_inference") or {
        "model": settings.LLM_MODEL_NAME, "online": False, "server_reachable": False,
        "mode": "Content service is unavailable",
    }
    system_status: Dict[str, Any] = {
        "status": "running" if content_ok and scheduler_ok else "degraded",
        "llm_inference": llm,
        "image_inference": {
            "model": content.get("image_model", settings.IMAGE_MODEL_NAME),
            "worker_mode": "Media service: images, video and speech, scaled separately",
        },
        "decision_engine": {
            "algorithm": "Factorized LinUCB cascade",
            "status": "active" if content_ok else "unavailable",
            "novelty": "decision dimensions factorized over a shared context",
        },
        "database": {
            "engine": "PostgreSQL 16 + pgvector, a database per service (SQLite for local runs)",
            "connected": True,
        },
        "services": {"content": content_ok, "scheduler": scheduler_ok, "telemetry": True},
        "event_bus": "RabbitMQ" if settings.RABBITMQ_URL else "in-process",
    }

    return DashboardMetricsResponse(
        system_status=system_status,
        total_posts=queue.get("total_posts", 0),
        published_posts=queue.get("published_posts", 0),
        total_telemetry_events=total_telemetry,
        active_experiments=content.get("active_experiments", 0),
        latest_experiment_summary=content.get("latest_experiment_summary"),
        recent_content=content.get("recent_content", []),
    )
