"""
Наскрізна перевірка взаємодії сервісів (режим SERVICE=all: ті самі REST-виклики
й події, лише без мережі між контейнерами).

    Content ──REST──► Scheduler ──REST──► Publishing ── post.published ──► Telemetry, Content
    Telemetry ── reward.computed ──► Content (винагорода для політики)
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

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
    import uuid

    item_id = uuid.uuid4().int % 10**9  # база тестів спільна між запусками — ідентифікатор унікальний
    async with AsyncSessionLocal() as db:
        decision = PolicyDecision(context_vector=[], chosen_arms={"topic": "x"}, content_item_id=item_id)
        db.add(decision)
        await db.commit()
        decision_id = decision.id

    event = {"id": f"test-{item_id}", "type": "reward.computed", "payload": {
        "post_id": 1, "content_item_id": item_id, "reward": 0.5, "computed_at": "2026-11-14T12:00:00+00:00",
    }}
    assert await events.dispatch("content", event) is True
    assert await events.dispatch("content", event) is False  # повторна доставка
    async with AsyncSessionLocal() as db:
        n = (await db.execute(
            select(func.count(PolicyReward.id)).where(PolicyReward.policy_decision_id == decision_id)
        )).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_media_limits_concurrent_generations(monkeypatch):
    """Media не бере більше MEDIA_CONCURRENCY генерацій одночасно — решта чекає в черзі."""
    import asyncio
    import threading
    import time

    from app.core.config import settings
    from app.generation.image_generator import ImageGenerator

    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(self, prompt, output_path, **kwargs):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.2)
        with lock:
            state["now"] -= 1
        return output_path

    monkeypatch.setattr(ImageGenerator, "generate_image", slow)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        responses = await asyncio.gather(*(
            ac.post("/api/v1/media/images", json={"folder": f"t{i}", "prompt": "p"}) for i in range(8)
        ))
    assert all(r.status_code == 200 for r in responses)
    assert state["max"] == settings.MEDIA_CONCURRENCY


@pytest.mark.asyncio
async def test_scheduler_queues_with_last_known_channels_when_publishing_is_down(monkeypatch):
    """Відмова Publishing не зупиняє постановку в чергу: список каналів збережено в БД Scheduler."""
    from app.api.v1 import schedule
    from app.core.clients import ServiceError
    from app.core.init_db import init_db

    await init_db()
    accounts = [{"id": 4242, "platform_code": "telegram", "external_account_id": "@chan", "status": "active"}]

    async def up(*args, **kwargs):
        return accounts

    async def down(*args, **kwargs):
        raise ServiceError("publishing", 503, "The publishing service is unavailable")

    async with AsyncSessionLocal() as db:
        monkeypatch.setattr(schedule, "call", up)
        assert await schedule._account(db, 4242) == accounts[0]
    monkeypatch.setattr(schedule, "_saved_channels", ())  # інша репліка: у пам'яті нічого немає
    monkeypatch.setattr(schedule, "call", down)
    async with AsyncSessionLocal() as db:
        acc = await schedule._account(db, 4242)  # сервіс лежить — канал відомий з власної БД
    assert (acc["platform_code"], acc["external_account_id"]) == ("telegram", "@chan")


@pytest.mark.asyncio
async def test_event_waits_in_outbox_while_broker_is_unreachable():
    """Поки брокера немає, подія лишається в outbox і буде відправлена пізніше."""
    from app.core.init_db import init_db

    await init_db()
    async with AsyncSessionLocal() as db:
        events.emit(db, "post.metrics", {"post_id": 1})
        await db.commit()
    bus = events.RabbitBus("amqp://unreachable", set())  # з'єднання ще не встановлено
    assert await events.relay_once(bus) == 0
    async with AsyncSessionLocal() as db:
        unsent = (await db.execute(select(func.count(OutboxEvent.id)).where(OutboxEvent.sent_at.is_(None)))).scalar()
    assert unsent >= 1
    async with AsyncSessionLocal() as db:  # прибрати за собою: інші тести перевіряють порожній outbox
        await db.execute(update(OutboxEvent).where(OutboxEvent.sent_at.is_(None)).values(sent_at=func.now()))
        await db.commit()
