"""Маршрути сервісу Publishing: публікація (для Scheduler), збір лічильників, можливості мереж."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.entities import Platform
from app.publishing import service

router = APIRouter(tags=["Publishing"])


class PublishRequest(BaseModel):
    post_id: int
    account_id: int
    content_item_id: int
    title: Optional[str] = None
    body: str = ""
    hashtags: List[str] = Field(default_factory=list)
    media_refs: Dict[str, Any] = Field(default_factory=dict)
    format: str = "text"


@router.post("/publishing/internal/publish")
async def publish_post(req: PublishRequest, db: AsyncSession = Depends(get_db)):
    """Одна спроба публікації; рішення про повтор приймає Scheduler."""
    account = await service.load_account(db, req.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Connected account not found")
    result = await service.publish(db, account, req.model_dump())
    return {
        "success": result.success,
        "external_post_id": result.external_post_id,
        "url": result.url,
        "error_message": result.error_message,
    }


@router.post("/telemetry/collect")
async def collect_now(db: AsyncSession = Depends(get_db)):
    """
    Примусове опитування лічильників усіх активних каналів.

    Той самий код, що виконує фоновий воркер, — доступний вручну, щоб не чекати
    наступного циклу під час демонстрації.
    """
    stats = await service.collect_all(db)
    return {"status": "success", **stats}


@router.get("/telemetry/capabilities")
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
