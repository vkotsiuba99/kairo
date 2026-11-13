"""
Журнал подій системи.

Окремої таблиці логів свідомо немає: усе, що відбувається, вже зафіксовано в
робочих сутностях — завданнях генерації, публікаціях, подіях телеметрії,
рішеннях політики та прогонах експерименту. Ендпоінт зводить їх в одну
хронологічну стрічку, тому журнал не може розійтися зі станом системи.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.entities import (
    ContentItem,
    Experiment,
    GenerationJob,
    PolicyDecision,
    Post,
    TelemetryEvent,
)

router = APIRouter(prefix="/logs", tags=["System Log"])

KIND_LABELS = {
    "generation": "Generation",
    "publication": "Publication",
    "telemetry": "Telemetry",
    "decision": "Policy decision",
    "experiment": "Experiment",
}


def _iso(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@router.get("")
async def list_logs(
    kind: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    entries: List[Dict[str, Any]] = []
    per_source = max(limit, 50)

    jobs = (
        await db.execute(
            select(GenerationJob).order_by(GenerationJob.requested_at.desc()).limit(per_source)
        )
    ).scalars().all()
    for job in jobs:
        failed = job.status == "failed"
        entries.append(
            {
                "id": f"gen-{job.id}",
                "at": _iso(job.completed_at or job.requested_at),
                "kind": "generation",
                "level": "error" if failed else ("success" if job.status == "completed" else "info"),
                "title": f"Generation job #{job.id}: {job.status}",
                "detail": job.error
                or f"type: {job.content_type}, backend: {job.backend}, model: {job.model_name}",
                "meta": {
                    "latency": f"{job.latency_ms} ms" if job.latency_ms else "—",
                    "cost": f"${float(job.cost_usd or 0):.4f}",
                },
            }
        )

    posts = (
        await db.execute(
            select(Post)
            .options(selectinload(Post.content_item), selectinload(Post.connected_account))
            .order_by(Post.id.desc())
            .limit(per_source)
        )
    ).scalars().all()
    for post in posts:
        level = {"published": "success", "failed": "error"}.get(post.status, "info")
        title_text = (post.content_item.title if post.content_item else None) or "untitled"
        entries.append(
            {
                "id": f"post-{post.id}",
                "at": _iso(post.published_at or post.scheduled_at),
                "kind": "publication",
                "level": level,
                "title": f"Publication #{post.id}: {post.status}",
                "detail": post.error_message or title_text,
                "meta": {
                    "channel": post.connected_account.external_account_id
                    if post.connected_account
                    else "—",
                    "external id": post.external_post_id or "—",
                },
            }
        )

    events = (
        await db.execute(
            select(TelemetryEvent).order_by(TelemetryEvent.id.desc()).limit(per_source)
        )
    ).scalars().all()
    for ev in events:
        entries.append(
            {
                "id": f"tel-{ev.id}",
                "at": _iso(ev.occurred_at),
                "kind": "telemetry",
                "level": "info",
                "title": f"Event: {ev.event_type}",
                "detail": f"publication #{ev.post_id}, value {float(ev.event_value or 0):g}",
                "meta": {},
            }
        )

    decisions = (
        await db.execute(
            select(PolicyDecision).order_by(PolicyDecision.id.desc()).limit(per_source)
        )
    ).scalars().all()
    for dec in decisions:
        arms = dec.chosen_arms or {}
        entries.append(
            {
                "id": f"dec-{dec.id}",
                "at": _iso(dec.decided_at),
                "kind": "decision",
                "level": "info",
                "title": f"Policy chose parameters for post #{dec.content_item_id}",
                "detail": ", ".join(f"{k}: {v}" for k, v in arms.items()) or "—",
                "meta": {"policy version": dec.policy_version or "—"},
            }
        )

    experiments = (
        await db.execute(select(Experiment).order_by(Experiment.id.desc()).limit(per_source))
    ).scalars().all()
    for exp in experiments:
        cfg = exp.config or {}
        entries.append(
            {
                "id": f"exp-{exp.id}",
                "at": _iso(exp.finished_at or exp.started_at),
                "kind": "experiment",
                "level": "success" if exp.finished_at else "info",
                "title": f"Experiment #{exp.id}: {exp.name}",
                "detail": f"rounds: {cfg.get('n_rounds', '—')}, seed: {cfg.get('seed', '—')}, α: {cfg.get('alpha', '—')}",
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
