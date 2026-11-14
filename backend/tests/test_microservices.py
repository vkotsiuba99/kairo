"""
Наскрізна перевірка взаємодії сервісів (режим SERVICE=all: ті самі REST-виклики
й події, лише без мережі між контейнерами).

    Content ──REST──► Scheduler ──REST──► Publishing ── post.published ──► Telemetry, Content
    Telemetry ── reward.computed ──► Content (винагорода для політики)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app import events
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.entities import ContentItem, OutboxEvent, PolicyDecision, PolicyReward, TrackedPost


@pytest.mark.asyncio
async def test_publish_and_reward_flow_across_services(monkeypatch):
    from app.core.init_db import init_db

    monkeypatch.setenv("KAIRO_IMAGE_TIER", "poster")  # ілюстрація без мережі
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        gen = await ac.post("/api/v1/content/generate", json={
            "platform_code": "local_archive", "topic": "ai_breakthroughs", "format_type": "text",
            "tone": "analytical", "length_bucket": "short",
        })
        assert gen.status_code == 200, gen.text
        item = gen.json()["content_item"]
        assert item["media_refs"]["image_url"].startswith(f"/media/")  # ілюстрацію зробив Media

        pub = await ac.post(f"/api/v1/content/{item['id']}/publish")
        assert pub.status_code == 200, pub.text
        external_id = pub.json()["external_post_id"]

        # подія post.published ще в outbox Publishing — сервіси дізнаються про неї після доставки
        await events.drain()
        async with AsyncSessionLocal() as db:
            assert (await db.get(ContentItem, item["id"])).status == "published"
            tracked = (await db.execute(
                select(TrackedPost).where(TrackedPost.content_item_id == item["id"])
            )).scalar_one()
            assert tracked.external_post_id == external_id

        ing = await ac.post("/api/v1/telemetry/ingest", json={
            "platform_code": "local_archive", "external_post_id": external_id,
            "event_type": "reaction", "event_value": 3,
        })
        assert ing.json()["matched_post_id"] == tracked.post_id
        await events.drain()

    async with AsyncSessionLocal() as db:
        decision = (await db.execute(
            select(PolicyDecision).where(PolicyDecision.content_item_id == item["id"])
        )).scalar_one()
        rewards = (await db.execute(
            select(func.count(PolicyReward.id)).where(PolicyReward.policy_decision_id == decision.id)
        )).scalar()
        assert rewards == 1  # винагорода з Telemetry дійшла до політики в Content
        pending = (await db.execute(select(func.count(OutboxEvent.id)).where(OutboxEvent.sent_at.is_(None)))).scalar()
        assert pending == 0


@pytest.mark.asyncio
async def test_redelivered_event_is_processed_once():
    """Брокер може доставити подію повторно — наслідки не дублюються."""
    from app.core.init_db import init_db

    await init_db()
    async with AsyncSessionLocal() as db:
        decision = PolicyDecision(context_vector=[], chosen_arms={"topic": "x"}, content_item_id=987654)
        db.add(decision)
        await db.commit()
        decision_id = decision.id

    event = {"id": f"test-{decision_id}", "type": "reward.computed", "payload": {
        "post_id": 1, "content_item_id": 987654, "reward": 0.5, "computed_at": "2026-11-14T12:00:00+00:00",
    }}
    assert await events.dispatch("content", event) is True
    assert await events.dispatch("content", event) is False  # повторна доставка
    async with AsyncSessionLocal() as db:
        n = (await db.execute(
            select(func.count(PolicyReward.id)).where(PolicyReward.policy_decision_id == decision_id)
        )).scalar()
    assert n == 1
