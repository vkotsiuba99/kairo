import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.models.entities import (
    ContentItem, GenerationJob, Platform, AudienceSegment, PolicyDecision, Post, ConnectedAccount
)
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

    # 7. Optionally auto-schedule post
    scheduled_post_id = None
    if req.auto_schedule_minutes is not None:
        from datetime import timedelta
        # Find active connected account for platform
        acc_res = await db.execute(
            select(ConnectedAccount).where(ConnectedAccount.platform_id == platform.id)
        )
        account = acc_res.scalars().first()
        if account:
            post = Post(
                content_item_id=content_item_id,
                connected_account_id=account.id,
                scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=req.auto_schedule_minutes),
                status="pending",
            )
            db.add(post)
            await db.flush()
            scheduled_post_id = post.id

    await db.commit()

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
    import time
    import os
    from app.core.config import settings
    from app.generation.image_generator import ImageGenerator

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

    image_gen = ImageGenerator()
    media_dir = os.path.join(settings.DATA_MEDIA_PATH, str(item.generation_job_id or item_id))
    os.makedirs(media_dir, exist_ok=True)
    img_path = os.path.join(media_dir, "keyframe_1.jpg")

    prompt = custom_prompt or (item.media_refs or {}).get("media_prompt") or item.title or "technology"
    # Генератор синхронний (мережевий запит до 75 с, PIL) — в окремому потоці,
    # інакше він зупиняє цикл подій і всі інші запити до API чекають.
    await asyncio.to_thread(
        image_gen.generate_image,
        prompt=prompt,
        output_path=img_path,
        topic=topic,
        title=item.title or "",
    )

    ts = int(time.time())
    new_url = f"/media/{item.generation_job_id or item_id}/keyframe_1.jpg?t={ts}"
    new_refs = dict(item.media_refs or {})
    new_refs["image"] = img_path
    new_refs["image_url"] = new_url
    new_refs["media_prompt"] = prompt
    item.media_refs = new_refs

    await db.commit()
    return {"success": True, "image_url": new_url, "media_prompt": prompt}


@router.post("/{item_id}/publish")
async def publish_content_item_endpoint(item_id: int, db: AsyncSession = Depends(get_db)):
    from app.scheduler.runner import publish_post

    res = await db.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.platform))
        .where(ContentItem.id == item_id)
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Content item not found")

    # Find connected account for this platform
    acc_res = await db.execute(
        select(ConnectedAccount)
        .options(selectinload(ConnectedAccount.platform))
        .where(ConnectedAccount.platform_id == item.platform_id)
    )
    account = acc_res.scalars().first()
    if not account:
        raise HTTPException(
            status_code=400,
            detail=f"No connected account for {item.platform.display_name if item.platform else item.platform_id}"
        )

    # Допис створюється одразу як «публікується» і проходить той самий шлях,
    # що й черга, тож телеметрія потім знайде його за external_post_id.
    now = datetime.now(timezone.utc)
    post = Post(
        content_item_id=item.id,
        connected_account_id=account.id,
        scheduled_at=now,
        status="publishing",
        claimed_at=now,
        attempts=0,
    )
    post.content_item, post.connected_account = item, account
    db.add(post)
    pub_result = await publish_post(db, post)
    if not pub_result.success:
        post.status = "failed"
    await db.commit()

    if not pub_result.success:
        raise HTTPException(status_code=424, detail=pub_result.error_message or "Publishing failed")
    return {
        "success": True,
        "external_post_id": pub_result.external_post_id,
        "url": pub_result.url,
        "message": f"Published. ID: {pub_result.external_post_id}",
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
