from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.models.entities import ConnectedAccount, Platform, Post, TelemetryEvent
from app.schemas.api import TelemetryIngestRequest
from app.decision.policy_state import invalidate as invalidate_policy
from app.telemetry.collector import recompute_reward_for_post

router = APIRouter(prefix="/telemetry", tags=["Telemetry Ingest"])


@router.post("/ingest")
async def ingest_telemetry_event(
    req: TelemetryIngestRequest,
    db: AsyncSession = Depends(get_db)
):
    plat_res = await db.execute(select(Platform).where(Platform.code == req.platform_code))
    platform = plat_res.scalar_one_or_none()
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{req.platform_code}' not found")

    # Match post by external_post_id if present
    post_res = await db.execute(
        select(Post)
        .options(
            selectinload(Post.content_item),
            selectinload(Post.connected_account).selectinload(ConnectedAccount.platform),
        )
        # Ідентифікатори дописів унікальні лише в межах платформи (у Telegram і
        # Mastodon обидва можуть бути «42»), тому збіг шукаємо в межах платформи.
        .join(ConnectedAccount, Post.connected_account_id == ConnectedAccount.id)
        .where(Post.external_post_id == req.external_post_id, ConnectedAccount.platform_id == platform.id)
    )
    post = post_res.scalars().first()

    event = TelemetryEvent(
        post_id=post.id if post else None,
        platform_id=platform.id,
        event_type=req.event_type,
        event_value=req.event_value,
        occurred_at=req.occurred_at or datetime.now(timezone.utc),
        raw_payload=req.raw_payload,
    )
    db.add(event)
    await db.flush()

    # Винагорода — тим самим кодом, що й у фоновому зборі телеметрії
    # (з поправкою ваг на метрики, доступні на платформі).
    reward_val = await recompute_reward_for_post(db, post) if post else None

    await db.commit()
    if reward_val is not None:
        invalidate_policy()
    return {
        "status": "success",
        "event_id": event.id,
        "matched_post_id": post.id if post else None,
        "computed_reward": reward_val,
    }


@router.post("/collect")
async def collect_now(db: AsyncSession = Depends(get_db)):
    """
    Примусовий збір телеметрії з усіх активних каналів.

    Той самий код, що виконує фоновий воркер, — доступний вручну, щоб не чекати
    наступного циклу під час демонстрації.
    """
    from app.telemetry.collector import collect_all

    stats = await collect_all(db)
    return {"status": "success", **stats}


@router.get("/capabilities")
async def telemetry_capabilities(db: AsyncSession = Depends(get_db)):
    """
    Що саме кожна платформа реально віддає.

    Дані беруться з самих адаптерів, а не з окремого списку в документації —
    тому інтерфейс не може розійтися з фактичною реалізацією.
    """
    from app.adapters.registry import BUILTIN_ADAPTERS

    platforms = (await db.execute(select(Platform))).scalars().all()
    out = []
    for platform in platforms:
        adapter_cls = BUILTIN_ADAPTERS.get(platform.code)
        if not adapter_cls:
            continue
        out.append(
            {
                "code": platform.code,
                "display_name": platform.display_name,
                "available": getattr(adapter_cls, "AVAILABLE", True),
                "unavailable_reason": getattr(adapter_cls, "UNAVAILABLE_REASON", ""),
                "counter_metrics": list(getattr(adapter_cls, "COUNTER_METRICS", []) or []),
                "per_actor": getattr(adapter_cls, "SUPPORTS_PER_ACTOR", False),
                "note": getattr(adapter_cls, "TELEMETRY_NOTE", ""),
            }
        )
    return out
