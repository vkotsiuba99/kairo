from typing import Dict, Any, List, Optional
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.config import settings
from app.models.entities import ContentItem, Post, TelemetryEvent, Experiment, SimulationRun
from app.schemas.api import DashboardMetricsResponse, ContentItemResponse

router = APIRouter(prefix="/dashboard", tags=["Dashboard Metrics"])


@router.get("/metrics", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(experiment_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    # Total posts
    post_count_res = await db.execute(select(func.count(Post.id)))
    total_posts = post_count_res.scalar() or 0

    pub_count_res = await db.execute(select(func.count(Post.id)).where(Post.status == "published"))
    published_posts = pub_count_res.scalar() or 0

    # Total telemetry events
    telem_count_res = await db.execute(select(func.count(TelemetryEvent.id)))
    total_telemetry = telem_count_res.scalar() or 0

    # Active experiments
    exp_count_res = await db.execute(select(func.count(Experiment.id)))
    total_experiments = exp_count_res.scalar() or 0

    # Latest experiment summary
    if experiment_id:
        exp_stmt = select(SimulationRun).where(SimulationRun.experiment_id == experiment_id)
    else:
        exp_stmt = select(SimulationRun).order_by(SimulationRun.id.desc())
    run_res = await db.execute(exp_stmt)
    latest_run = run_res.scalars().first()
    latest_summary = latest_run.results if latest_run else None

    # Recent content items
    ci_res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform))
        .order_by(ContentItem.created_at.desc())
        .limit(6)
    )
    items = ci_res.scalars().all()
    recent_content = [
        ContentItemResponse(
            id=ci.id,
            platform_id=ci.platform_id,
            platform_code=ci.platform.code if ci.platform else "unknown",
            format=ci.format,
            tone=ci.tone,
            length_bucket=ci.length_bucket,
            title=ci.title,
            body=ci.body,
            hashtags=ci.hashtags or [],
            media_refs=ci.media_refs or {},
            status=ci.status,
            created_at=ci.created_at,
        )
        for ci in items
    ]

    # Стан локального інференсу.
    #
    # Наявність відповіді на /models ще не означає, що модель працює: LM Studio
    # відповідає на перелік навіть тоді, коли жоден чекпоінт не піднімається.
    # Тому враховується ще й запобіжник генерації — інакше дешборд показував би
    # «локальний інференс на GPU» у той час, коли всі матеріали пише хмара.
    from app.generation.llm_client import local_backend_state

    server_reachable = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{settings.LLM_BASE_URL}/models")
            server_reachable = resp.is_success
    except Exception:
        server_reachable = False

    breaker = local_backend_state()
    llm_online = server_reachable and not breaker["disabled"]

    if not server_reachable:
        llm_mode = "LM Studio server is not responding — running on the cloud fallback"
    elif breaker["disabled"]:
        llm_mode = (
            f"Model {settings.LLM_MODEL_NAME} is not loading — running on the cloud fallback; "
            f"retry in {breaker['seconds_left']} s"
        )
    else:
        llm_mode = f"{settings.LLM_MODEL_NAME}, local GPU inference"

    system_status = {
        "status": "running",
        "llm_inference": {
            "model": settings.LLM_MODEL_NAME,
            "endpoint": settings.LLM_BASE_URL,
            "online": llm_online,
            "server_reachable": server_reachable,
            "mode": llm_mode,
        },
        "image_inference": {
            "model": settings.IMAGE_MODEL_NAME,
            "worker_mode": "in-process async generation; images in a thread pool",
        },
        "decision_engine": {
            "algorithm": "Factorized LinUCB cascade",
            "status": "active",
            "novelty": "decision dimensions factorized over a shared context",
        },
        "database": {
            "engine": "PostgreSQL 16 + pgvector (SQLite fallback for local runs)",
            "connected": True,
        }
    }

    return DashboardMetricsResponse(
        system_status=system_status,
        total_posts=total_posts,
        published_posts=published_posts,
        total_telemetry_events=total_telemetry,
        active_experiments=total_experiments,
        latest_experiment_summary=latest_summary,
        recent_content=recent_content,
    )
