"""
Журнал подій системи (сервіс Telemetry).

Окремої таблиці логів свідомо немає: усе, що відбувається, вже зафіксовано в
робочих сутностях сервісів. Content віддає генерації, рішення політики й
експерименти, Scheduler — публікації, Telemetry — події реакцій; ендпоінт
зводить їх в одну хронологічну стрічку. Дані сервісів сходяться з невеликою
затримкою (події між ними асинхронні), тож журнал показує стан кожного
сервісу на момент запиту.
"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import iso
from app.core.clients import ServiceError, call
from app.core.database import get_db
from app.models.entities import TelemetryEvent

router = APIRouter(prefix="/logs", tags=["System Log"])

KIND_LABELS = {
    "generation": "Generation",
    "publication": "Publication",
    "telemetry": "Telemetry",
    "decision": "Policy decision",
    "experiment": "Experiment",
}


async def _remote(service: str, path: str, limit: int) -> List[Dict[str, Any]]:
    try:
        return await call(service, "GET", path, params={"limit": limit})
    except ServiceError:
        return []  # сервіс недоступний — журнал показує решту


@router.get("")
async def list_logs(
    kind: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    per_source = max(limit, 50)
    content_entries, queue_entries = await asyncio.gather(
        _remote("content", "/content/internal/activity", per_source),
        _remote("scheduler", "/schedule/internal/activity", per_source),
    )
    entries: List[Dict[str, Any]] = content_entries + queue_entries

    events = (
        await db.execute(select(TelemetryEvent).order_by(TelemetryEvent.id.desc()).limit(per_source))
    ).scalars().all()
    for ev in events:
        entries.append(
            {
                "id": f"tel-{ev.id}",
                "at": iso(ev.occurred_at),
                "kind": "telemetry",
                "level": "info",
                "title": f"Event: {ev.event_type}",
                "detail": f"publication #{ev.post_id}, value {float(ev.event_value or 0):g}",
                "meta": {},
            }
        )

    if kind:
        entries = [e for e in entries if e["kind"] == kind]
    if level:
        entries = [e for e in entries if e["level"] == level]

    entries.sort(key=lambda e: e["at"] or "", reverse=True)
    entries = entries[:limit]

    counts: Dict[str, int] = {}
    for e in entries:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1

    return {
        "total": len(entries),
        "counts": counts,
        "kind_labels": KIND_LABELS,
        "entries": entries,
    }
