"""Маршрути сервісу Telemetry: прийом подій реакцій (вебхуки або ручне введення)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.entities import TelemetryEvent, TrackedPost
from app.schemas.api import TelemetryIngestRequest
from app.telemetry.service import recompute_reward

router = APIRouter(prefix="/telemetry", tags=["Telemetry Ingest"])


@router.post("/ingest")
async def ingest_telemetry_event(req: TelemetryIngestRequest, db: AsyncSession = Depends(get_db)):
    # Ідентифікатори дописів унікальні лише в межах платформи (у Telegram і
    # Mastodon обидва можуть бути «42»), тому збіг шукаємо в межах платформи.
    tracked = (
        await db.execute(
            select(TrackedPost).where(
                TrackedPost.external_post_id == req.external_post_id,
                TrackedPost.platform_code == req.platform_code,
            )
        )
    ).scalars().first()

    event = TelemetryEvent(
        post_id=tracked.post_id if tracked else None,
        platform_code=req.platform_code,
        event_type=req.event_type,
        event_value=req.event_value,
        occurred_at=req.occurred_at or datetime.now(timezone.utc),
        raw_payload=req.raw_payload,
    )
    db.add(event)
    await db.flush()

    # Винагорода — тим самим кодом, що й для подій post.metrics; у Content вона
    # потрапить подією reward.computed
    reward_val = await recompute_reward(db, tracked) if tracked else None
    await db.commit()
    return {
        "status": "success",
        "event_id": event.id,
        "matched_post_id": tracked.post_id if tracked else None,
        "computed_reward": reward_val,
    }
