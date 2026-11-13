import pytest
from httpx import AsyncClient, ASGITransport

from app.generation.image_generator import ImageGenerator
from app.main import app


@pytest.mark.asyncio
async def test_regenerate_image_passes_topic_not_tone(monkeypatch):
    from app.core.init_db import init_db
    await init_db()
    monkeypatch.setenv("KAIRO_IMAGE_TIER", "poster")  # без мережі
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        gen = await ac.post("/api/v1/content/generate", json={
            "platform_code": "telegram", "topic": "ai_breakthroughs", "format_type": "text",
            "tone": "analytical", "length_bucket": "short",
        })
        assert gen.status_code == 200
        item_id = gen.json()["content_item"]["id"]

        seen = {}

        def fake_generate(self, prompt, output_path, width=1200, height=800, topic="", title=""):
            seen["topic"] = topic
            return output_path

        monkeypatch.setattr(ImageGenerator, "generate_image", fake_generate)
        r = await ac.post(f"/api/v1/content/{item_id}/regenerate-image", json={})
        assert r.status_code == 200
    assert seen["topic"] == "ai_breakthroughs"
