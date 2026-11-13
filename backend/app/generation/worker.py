import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy import select, update
from app.core.database import AsyncSessionLocal
from app.core.config import settings
from app.models.entities import GenerationJob, ContentItem, Platform
from app.generation.llm_client import MultiBackendLLMClient
from app.generation.image_generator import ImageGenerator
from app.generation.video_composer import VideoComposer

logger = logging.getLogger("kairo.worker")


async def process_generation_job_async(
    job_id: int,
    platform_code: str,
    topic: str,
    format_type: str,
    tone: str,
    length_bucket: str,
    backend_pref: str = "local",
    audience_interests: str = "",
    language: str = "en",
) -> Dict[str, Any]:
    """
    Виконує завдання генерації в процесі API (асинхронно): текст — LLM-клієнт
    з локальним/хмарним/офлайн-тиром, ілюстрація — у пулі потоків.
    """
    llm_client = MultiBackendLLMClient()
    image_gen = ImageGenerator()
    video_comp = VideoComposer()

    async with AsyncSessionLocal() as session:
        # Mark job as processing
        await session.execute(
            update(GenerationJob)
            .where(GenerationJob.id == job_id)
            .values(status="processing")
        )
        await session.commit()

    try:
        # 1. Generate text
        t_result = await llm_client.generate_content(
            platform_code=platform_code,
            topic=topic,
            format_type=format_type,
            tone=tone,
            length_bucket=length_bucket,
            backend_preference=backend_pref,
            audience_interests=audience_interests,
            language=language,
        )

        media_refs = {}
        # 2. Generate visual assets (AI illustration for the post)
        try:
            media_dir = os.path.join(settings.DATA_MEDIA_PATH, str(job_id))
            os.makedirs(media_dir, exist_ok=True)

            img_prompt = t_result.content.media_suggestion or f"High quality cinematic digital artwork illustrating {topic}, 8k, photorealistic"
            img_path = os.path.join(media_dir, "keyframe_1.jpg")
            # синхронний генератор — в окремому потоці, щоб не блокувати цикл подій API
            await asyncio.to_thread(
                image_gen.generate_image,
                prompt=img_prompt,
                output_path=img_path,
                topic=topic,
                title=t_result.content.title or "",
            )
            media_refs["image"] = img_path
            media_refs["image_url"] = f"/media/{job_id}/keyframe_1.jpg"
            media_refs["media_prompt"] = img_prompt

            # 3. If video, compose audio + frame
            if format_type == "video_short":
                vid_path = os.path.join(media_dir, "video.mp4")
                await video_comp.compose_video(
                    script_text=t_result.content.body,
                    keyframe_image_paths=[img_path],
                    output_mp4_path=vid_path,
                    is_vertical_shorts=True,
                )
                media_refs["video"] = vid_path
                media_refs["video_url"] = f"/media/{job_id}/video.mp4"
        except Exception as e:
            logger.warning("Visual asset generation warning: %s", e)

        # 4. Save ContentItem and update GenerationJob
        async with AsyncSessionLocal() as session:
            # Find platform
            plat_res = await session.execute(select(Platform).where(Platform.code == platform_code))
            platform = plat_res.scalar_one_or_none()
            if not platform:
                # Default to first platform or local_archive
                plat_res = await session.execute(select(Platform))
                platform = plat_res.scalars().first()

            content_item = ContentItem(
                generation_job_id=job_id,
                platform_id=platform.id if platform else 1,
                format=format_type,
                tone=tone,
                length_bucket=length_bucket,
                title=t_result.content.title,
                body=t_result.content.body,
                hashtags=t_result.content.hashtags,
                media_refs=media_refs,
                status="ready",
                created_at=datetime.now(timezone.utc),
            )
            session.add(content_item)
            await session.flush()

            # Update job
            await session.execute(
                update(GenerationJob)
                .where(GenerationJob.id == job_id)
                .values(
                    content_item_id=content_item.id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc),
                    latency_ms=t_result.latency_ms,
                    cost_usd=t_result.cost_usd,
                    backend=t_result.backend_used,
                    model_name=t_result.model_name,
                )
            )
            await session.commit()

            return {
                "success": True,
                "job_id": job_id,
                "content_item_id": content_item.id,
                "title": content_item.title,
                "backend": t_result.backend_used,
                "cost_usd": t_result.cost_usd,
                "latency_ms": t_result.latency_ms,
            }

    except Exception as e:
        logger.error("Job %s execution failed: %s", job_id, e, exc_info=True)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(GenerationJob)
                .where(GenerationJob.id == job_id)
                .values(
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                    error=str(e),
                )
            )
            await session.commit()
        return {"success": False, "job_id": job_id, "error": str(e)}
