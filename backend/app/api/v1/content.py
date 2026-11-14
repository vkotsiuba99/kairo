from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.models.entities import (
    ContentItem, GenerationJob, Platform, AudienceSegment, PolicyDecision, Experiment, SimulationRun
)
from fastapi.encoders import jsonable_encoder
from app.core.activity import iso
from app.core.clients import ServiceError, call
from app.schemas.api import GenerateContentRequest, ContentItemResponse, ContentItemUpdateRequest
from app.decision.policy_state import POLICY_VERSION, get_bandit
from app.audience.features import build_context_vector
from app.generation.worker import process_generation_job_async

router = APIRouter(prefix="/content", tags=["Content Generation"])


@router.post("/generate")
async def generate_content_endpoint(
    req: GenerateContentRequest,
    db: AsyncSession = Depends(get_db)
):
    # 1. Validate platform
    plat_res = await db.execute(select(Platform).where(Platform.code == req.platform_code))
    platform = plat_res.scalar_one_or_none()
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{req.platform_code}' not found")

    # 2. Retrieve segment if specified
    segment_centroid = None
    audience_interests = ""
    language = "en"  # мова матеріалу — з портрета сегмента
    if req.segment_id:
        seg_res = await db.execute(select(AudienceSegment).where(AudienceSegment.id == req.segment_id))
        segment = seg_res.scalar_one_or_none()
        if segment:
            from app.audience.profiles import describe_profile

            segment_centroid = segment.centroid
            language = (segment.profile or {}).get("language") or "en"
            description = describe_profile(segment.profile or {})
            # Модель отримує повний портрет, а не саму лише назву сегмента:
            # «Школярі, до 18 років, інтереси: риболовля, музика» дає інший
            # текст, ніж просто «Школярі».
            audience_interests = f"{segment.label}. {description}" if description else segment.label

    # 3. Decision Engine: Contextual Bandit arm selection
    context_vector = build_context_vector(segment_centroid, req.platform_code)
    # Стан політики відтворюється з БД (рішення + отримані винагороди), тож навчання
    # на реальній телеметрії бачать усі репліки API, а не лише цей процес.
    bandit = await get_bandit(db)
    chosen_arms, scores, _ = bandit.select(context_vector, explore=True)

    # Allow user explicit overrides if provided in request
    final_topic = req.topic or chosen_arms.get("topic", "ai_breakthroughs")
    final_format = req.format_type or chosen_arms.get("format", "text")
    final_tone = req.tone or chosen_arms.get("tone", "analytical")
    final_length = req.length_bucket or chosen_arms.get("length", "medium")
    final_backend = req.backend or chosen_arms.get("backend", "local")

    # 4. Create GenerationJob entry
    job = GenerationJob(
        content_type="video" if final_format == "video_short" else "image" if final_format == "image_post" else "text",
        backend=final_backend,
        model_name="ternary-bonsai-27b",
        status="pending",
        requested_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # 5. Execute generation synchronously or in worker
    result = await process_generation_job_async(
        job_id=job.id,
        platform_code=req.platform_code,
        topic=final_topic,
        format_type=final_format,
        tone=final_tone,
        length_bucket=final_length,
        backend_pref=final_backend,
        audience_interests=audience_interests,
        language=language,
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Generation failed"))

    content_item_id = result["content_item_id"]

    # 6. Record PolicyDecision
    policy_decision = PolicyDecision(
        content_item_id=content_item_id,
        segment_id=req.segment_id,
        context_vector=context_vector,
        chosen_arms={
            "topic": final_topic,
            "format": final_format,
            "tone": final_tone,
            "length": final_length,
            "time_slot": chosen_arms.get("time_slot", ""),
            "backend": final_backend,
        },
        policy_version=POLICY_VERSION,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(policy_decision)

    await db.commit()

    # 7. Автопланування: допис ставить у чергу сервіс Scheduler
    scheduled_post_id = None
    if req.auto_schedule_minutes is not None:
        snapshot = await get_content_item(content_item_id, db)
        try:
            queued = await call("scheduler", "POST", "/schedule/internal/enqueue", json={
                "content_item": jsonable_encoder(snapshot),
                "delay_minutes": req.auto_schedule_minutes,
            })
            scheduled_post_id = queued["post"]["id"]
        except ServiceError as e:
            if e.status_code != 400:  # 400 — для мережі немає каналу: матеріал лишається без публікації
                raise

    # Query created content item
    ci_res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform))
        .where(ContentItem.id == content_item_id)
    )
    item = ci_res.scalar_one()

    return {
        "content_item": ContentItemResponse(
            id=item.id,
            platform_id=item.platform_id,
            platform_code=item.platform.code,
            format=item.format,
            tone=item.tone,
            length_bucket=item.length_bucket,
            title=item.title,
            body=item.body,
            hashtags=item.hashtags,
            media_refs=item.media_refs,
            status=item.status,
            created_at=item.created_at,
        ),
        "decision": {
            "chosen_arms": policy_decision.chosen_arms,
            "bandit_predicted_scores": scores,
            "backend": final_backend,
        },
        "generation_job": {
            "id": job.id,
            "latency_ms": result.get("latency_ms"),
            "cost_usd": result.get("cost_usd", 0.0),
        },
        "scheduled_post_id": scheduled_post_id,
    }


@router.get("", response_model=List[ContentItemResponse])
async def list_content_items(limit: int = 50, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform))
        .order_by(ContentItem.created_at.desc())
        .limit(limit)
    )
    items = res.scalars().all()
    return [
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


@router.get("/internal/overview")
async def content_overview(experiment_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    """Частина дашборду, що належить Content: експерименти, останні матеріали, стан моделей."""
    import httpx
    from sqlalchemy import func
    from app.core.config import settings
    from app.generation.llm_client import local_backend_state

    total_experiments = (await db.execute(select(func.count(Experiment.id)))).scalar() or 0
    if experiment_id:
        run_stmt = select(SimulationRun).where(SimulationRun.experiment_id == experiment_id)
    else:
        run_stmt = select(SimulationRun).order_by(SimulationRun.id.desc())
    latest_run = (await db.execute(run_stmt)).scalars().first()
    recent = await list_content_items(limit=6, db=db)

    # Наявність відповіді на /models ще не означає, що модель працює: LM Studio
    # відповідає на перелік навіть тоді, коли жоден чекпоінт не піднімається.
    # Тому враховується ще й запобіжник генерації.
    server_reachable = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            server_reachable = (await client.get(f"{settings.LLM_BASE_URL}/models")).is_success
    except Exception:
        server_reachable = False
    breaker = local_backend_state()
    if not server_reachable:
        llm_mode = "LM Studio server is not responding — running on the cloud fallback"
    elif breaker["disabled"]:
        llm_mode = (
            f"Model {settings.LLM_MODEL_NAME} is not loading — running on the cloud fallback; "
            f"retry in {breaker['seconds_left']} s"
        )
    else:
        llm_mode = f"{settings.LLM_MODEL_NAME}, local GPU inference"

    return {
        "active_experiments": total_experiments,
        "latest_experiment_summary": latest_run.results if latest_run else None,
        "recent_content": jsonable_encoder(recent),
        "llm_inference": {
            "model": settings.LLM_MODEL_NAME,
            "endpoint": settings.LLM_BASE_URL,
            "online": server_reachable and not breaker["disabled"],
            "server_reachable": server_reachable,
            "mode": llm_mode,
        },
        "image_model": settings.IMAGE_MODEL_NAME,
    }


@router.get("/internal/activity")
async def content_activity(limit: int = 200, db: AsyncSession = Depends(get_db)):
    """Записи журналу подій: генерації, рішення політики, експерименти (зводить Telemetry)."""
    entries = []
    jobs = (
        await db.execute(select(GenerationJob).order_by(GenerationJob.requested_at.desc()).limit(limit))
    ).scalars().all()
    for job in jobs:
        failed = job.status == "failed"
        entries.append({
            "id": f"gen-{job.id}",
            "at": iso(job.completed_at or job.requested_at),
            "kind": "generation",
            "level": "error" if failed else ("success" if job.status == "completed" else "info"),
            "title": f"Generation job #{job.id}: {job.status}",
            "detail": job.error or f"type: {job.content_type}, backend: {job.backend}, model: {job.model_name}",
            "meta": {
                "latency": f"{job.latency_ms} ms" if job.latency_ms else "—",
                "cost": f"${float(job.cost_usd or 0):.4f}",
            },
        })
    decisions = (
        await db.execute(select(PolicyDecision).order_by(PolicyDecision.id.desc()).limit(limit))
    ).scalars().all()
    for dec in decisions:
        arms = dec.chosen_arms or {}
        entries.append({
            "id": f"dec-{dec.id}",
            "at": iso(dec.decided_at),
            "kind": "decision",
            "level": "info",
            "title": f"Policy chose parameters for post #{dec.content_item_id}",
            "detail": ", ".join(f"{k}: {v}" for k, v in arms.items()) or "—",
            "meta": {"policy version": dec.policy_version or "—"},
        })
    experiments = (await db.execute(select(Experiment).order_by(Experiment.id.desc()).limit(limit))).scalars().all()
    for exp in experiments:
        cfg = exp.config or {}
        entries.append({
            "id": f"exp-{exp.id}",
            "at": iso(exp.finished_at or exp.started_at),
            "kind": "experiment",
            "level": "success" if exp.finished_at else "info",
            "title": f"Experiment #{exp.id}: {exp.name}",
            "detail": f"rounds: {cfg.get('n_rounds', '—')}, seed: {cfg.get('seed', '—')}, α: {cfg.get('alpha', '—')}",
            "meta": {},
        })
    return entries


@router.get("/{item_id}", response_model=ContentItemResponse)
async def get_content_item(item_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform))
        .where(ContentItem.id == item_id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")

    return ContentItemResponse(
        id=item.id,
        platform_id=item.platform_id,
        platform_code=item.platform.code if item.platform else "unknown",
        format=item.format,
        tone=item.tone,
        length_bucket=item.length_bucket,
        title=item.title,
        body=item.body,
        hashtags=item.hashtags or [],
        media_refs=item.media_refs or {},
        status=item.status,
        created_at=item.created_at,
    )


@router.put("/{item_id}", response_model=ContentItemResponse)
async def update_content_item_endpoint(
    item_id: int,
    req: ContentItemUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform))
        .where(ContentItem.id == item_id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")

    if req.title is not None:
        item.title = req.title
    if req.body is not None:
        item.body = req.body
    if req.hashtags is not None:
        item.hashtags = req.hashtags

    await db.commit()
    await db.refresh(item)
    return ContentItemResponse(
        id=item.id,
        platform_id=item.platform_id,
        platform_code=item.platform.code if item.platform else "unknown",
        format=item.format,
        tone=item.tone,
        length_bucket=item.length_bucket,
        title=item.title,
        body=item.body,
        hashtags=item.hashtags or [],
        media_refs=item.media_refs or {},
        status=item.status,
        created_at=item.created_at,
    )


@router.post("/{item_id}/regenerate-image")
async def regenerate_image_endpoint(
    item_id: int,
    custom_prompt: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform), selectinload(ContentItem.policy_decision))
        .where(ContentItem.id == item_id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")
    # тема не зберігається в ContentItem (там лише тон, формат, обсяг) — її записано в рішенні політики
    topic = ((item.policy_decision.chosen_arms or {}).get("topic") if item.policy_decision else "") or ""
    prompt = custom_prompt or (item.media_refs or {}).get("media_prompt") or item.title or "technology"

    image = await call("media", "POST", "/media/images", json={
        "folder": str(item.generation_job_id or item_id),
        "prompt": prompt,
        "topic": topic,
        "title": item.title or "",
    })
    item.media_refs = {**(item.media_refs or {}), **image}
    await db.commit()
    return {"success": True, "image_url": image["image_url"], "media_prompt": image["media_prompt"]}


@router.post("/{item_id}/publish")
async def publish_content_item_endpoint(item_id: int, db: AsyncSession = Depends(get_db)):
    """Публікує матеріал одразу: Scheduler ставить його в чергу й захоплює, Publishing публікує."""
    item = await get_content_item(item_id, db)
    res = await call("scheduler", "POST", "/schedule/internal/enqueue", json={
        "content_item": jsonable_encoder(item),
        "publish_now": True,
    })
    return {
        "success": True,
        "external_post_id": res.get("external_post_id"),
        "url": res.get("url"),
        "message": f"Published. ID: {res.get('external_post_id')}",
    }

@router.post("/topics/suggest")
async def suggest_topics_endpoint(
    segment_id: Optional[int] = None,
    count: int = 8,
    db: AsyncSession = Depends(get_db),
):
    """
    Теми під конкретну аудиторію.

    Фіксованого списку тем у системі немає: набір щоразу виводиться з портрета
    обраного сегмента, тому він змінюється разом з аудиторією.
    """
    from app.audience.profiles import describe_profile
    from app.generation.llm_client import MultiBackendLLMClient

    description = ""
    interests: List[str] = []
    label = "general audience"

    if segment_id:
        seg = (
            await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id))
        ).scalar_one_or_none()
        if not seg:
            raise HTTPException(status_code=404, detail="Segment not found")
        profile = seg.profile or {}
        label = seg.label
        interests = profile.get("interests") or []
        description = f"{seg.label}. {describe_profile(profile)}" if profile else seg.label

    client = MultiBackendLLMClient()
    topics = await client.suggest_topics(
        audience_description=description,
        interests=interests,
        count=max(3, min(count, 12)),
    )
    return {"segment_id": segment_id, "segment_label": label, "topics": topics}
