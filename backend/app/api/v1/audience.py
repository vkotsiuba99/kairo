from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audience.clustering import cluster_audience_embeddings
from app.audience.embedding import get_embedding
from app.audience.profiles import (
    build_activity_hours,
    describe_profile,
    peak_hours,
    taxonomy,
    timezone_offset,
)
from app.core.database import get_db
from app.models.entities import AudienceProfile, AudienceSegment

router = APIRouter(prefix="/audience", tags=["Audience & Segments"])


class SegmentProfileIn(BaseModel):
    """Портрет сегмента: лише вибір із довідників, без вільного тексту."""

    name: str = Field(..., description="A name from SEGMENT_NAMES")
    age: Optional[str] = None
    occupation: Optional[str] = None
    region: Optional[str] = None
    language: Optional[str] = None
    device: Optional[str] = None
    interests: List[str] = Field(default_factory=list)
    activity_windows: List[str] = Field(default_factory=list)
    size: Optional[str] = None


def _segment_payload(seg: AudienceSegment) -> Dict[str, Any]:
    profile = seg.profile or {}
    hours = seg.activity_hours or []
    return {
        "id": seg.id,
        "label": seg.label,
        "size": seg.size,
        "updated_at": seg.updated_at,
        "source": getattr(seg, "source", None) or "clustering",
        "profile": profile,
        "activity_hours": hours,
        "peak_hours": peak_hours(hours) if hours else [],
        "utc_offset": timezone_offset(profile) if profile else 0,
        "description": describe_profile(profile) if profile else "",
        "centroid_sample": (seg.centroid[:8] if seg.centroid else []),
    }


@router.get("/taxonomy")
async def get_taxonomy():
    """Довідники для форми портрета аудиторії."""
    return taxonomy()


@router.get("/segments")
async def list_segments(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(AudienceSegment).order_by(AudienceSegment.size.desc()))
    return [_segment_payload(s) for s in res.scalars().all()]


@router.post("/segments")
async def create_segment(req: SegmentProfileIn, db: AsyncSession = Depends(get_db)):
    profile = req.model_dump(exclude={"name"})
    activity = build_activity_hours(profile)

    # Вектор сегмента будується з його ж опису — жодних прихованих даних.
    embedding = await get_embedding(f"{req.name}. {describe_profile(profile)}")

    seg = AudienceSegment(
        label=req.name,
        centroid=embedding,
        size=int(req.size or 0),
        updated_at=datetime.now(timezone.utc),
        profile=profile,
        activity_hours=activity,
        source="manual",
    )
    db.add(seg)
    await db.commit()
    await db.refresh(seg)
    return _segment_payload(seg)


@router.put("/segments/{segment_id}")
async def update_segment(segment_id: int, req: SegmentProfileIn, db: AsyncSession = Depends(get_db)):
    seg = (
        await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id))
    ).scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    profile = req.model_dump(exclude={"name"})
    seg.label = req.name
    seg.profile = profile
    seg.activity_hours = build_activity_hours(profile)
    seg.size = int(req.size or 0)
    seg.centroid = await get_embedding(f"{req.name}. {describe_profile(profile)}")
    seg.source = "manual"
    seg.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(seg)
    return _segment_payload(seg)


@router.delete("/segments/{segment_id}")
async def delete_segment(segment_id: int, db: AsyncSession = Depends(get_db)):
    seg = (
        await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id))
    ).scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    await db.delete(seg)
    await db.commit()
    return {"status": "deleted", "id": segment_id}


@router.post("/cluster")
async def trigger_clustering(db: AsyncSession = Depends(get_db)):
    """
    Автоматична сегментація за зібраними профілями підписників.

    Працює лише тоді, коли телеметрія вже накопичена: якщо профілів немає,
    метод чесно повідомляє про це, а не вигадує синтетичні дані.
    """
    profiles = (await db.execute(select(AudienceProfile))).scalars().all()
    embeddings = [p.interest_embedding for p in profiles if p.interest_embedding]

    if len(embeddings) < 10:
        return {
            "status": "not_enough_data",
            "total_profiles": len(profiles),
            "message": (
                "Not enough follower profiles to cluster "
                f"({len(embeddings)} of at least 10). Clusters will appear once "
                "telemetry arrives from published posts. Until then, define the "
                "target audience manually."
            ),
        }

    segment_infos = cluster_audience_embeddings(embeddings, min_cluster_size=5)

    existing = {
        s.label: s
        for s in (await db.execute(select(AudienceSegment))).scalars().all()
    }

    touched: List[str] = []
    for info in segment_infos:
        seg = existing.get(info.label)
        if seg is None:
            seg = AudienceSegment(label=info.label)
            db.add(seg)
        seg.centroid = info.centroid
        seg.size = info.size
        seg.updated_at = datetime.now(timezone.utc)
        seg.source = "clustering"
        touched.append(info.label)

    await db.commit()
    return {
        "status": "success",
        "segments_created": touched,
        "segments_count": len(touched),
        "total_profiles": len(profiles),
    }
