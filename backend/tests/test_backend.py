import pytest
import os
import shutil
from datetime import datetime, timezone
import numpy as np
from httpx import AsyncClient, ASGITransport

from app.core.security import encrypt_credentials, decrypt_credentials
from app.audience.clustering import cluster_audience_embeddings
from app.decision.linucb import LinUCBHead
from app.decision.factorized_bandit import FactorizedContextualBandit
from app.decision.baselines import RandomPolicy, FixedHeuristicPolicy, GreedyHistoricalAveragePolicy
from app.decision.feedback import compute_reward
from app.simulation.audience_model import SyntheticAudienceSegment, SegmentSimulationConfig
from app.simulation.simulator import MultiPolicySimulator
from app.adapters.local_archive import LocalArchiveAdapter
from app.main import app


def test_encryption_roundtrip():
    data = {"bot_token": "123456:ABC-DEF", "chat_id": "@mychannel"}
    encrypted = encrypt_credentials(data)
    assert isinstance(encrypted, str)
    assert len(encrypted) > 0

    decrypted = decrypt_credentials(encrypted)
    assert decrypted == data


def test_linucb_selection_and_update():
    head = LinUCBHead("test_dim", ["arm_a", "arm_b", "arm_c"], feature_dim=8, alpha=1.0)
    ctx = np.array([1.0, 0.5, -0.2, 0.0, 0.1, 0.9, -0.4, 0.2])

    arm, score = head.select_arm(ctx, explore=True)
    assert arm in ["arm_a", "arm_b", "arm_c"]
    assert isinstance(score, float)

    # Reward arm_b multiple times
    for _ in range(10):
        head.update("arm_b", ctx, reward=5.0)

    # Now arm_b should be selected with higher expected score
    chosen_greedy, g_score = head.select_arm(ctx, explore=False)
    assert chosen_greedy == "arm_b"


def test_factorized_bandit_cascade():
    bandit = FactorizedContextualBandit(base_context_dim=16, alpha=1.0)
    ctx = [0.1] * 16

    chosen_arms, scores, cascaded = bandit.select(ctx, explore=True)
    assert "topic" in chosen_arms
    assert "format" in chosen_arms
    assert "tone" in chosen_arms
    assert "length" in chosen_arms
    assert "time_slot" in chosen_arms
    assert "backend" in chosen_arms

    assert len(cascaded) == len(bandit.dim_order)

    # Test update
    bandit.update(cascaded, chosen_arms, reward=1.0)


def test_audience_clustering_hdbscan():
    # 2 distinct clusters
    c1 = np.random.randn(15, 768) + 2.0
    c2 = np.random.randn(15, 768) - 2.0
    embeddings = np.vstack([c1, c2]).tolist()

    segments = cluster_audience_embeddings(embeddings, min_cluster_size=5)
    assert len(segments) >= 1
    assert all(len(s.centroid) == 768 for s in segments)
    assert sum(s.size for s in segments) == 30


def test_multi_policy_simulation():
    sim = MultiPolicySimulator(n_rounds=20, seed=42)
    res = sim.run_simulation()

    assert "summary" in res
    assert "bandit" in res["summary"]
    assert "random" in res["summary"]
    assert "fixed_heuristic" in res["summary"]
    assert "greedy_avg" in res["summary"]

    assert len(res["history"]["bandit"]["cumulative_reward"]) == 20
    assert len(res["history"]["bandit"]["cumulative_regret"]) == 20


@pytest.mark.asyncio
async def test_local_archive_adapter(tmp_path):
    adapter = LocalArchiveAdapter({"archive_dir": str(tmp_path)})
    result = await adapter.publish(
        content_item_id=999,
        title="Test Post",
        body="This is an archival test",
        media_refs={},
        hashtags=["test", "ai"],
        format_type="text",
    )
    assert result.success is True
    assert result.external_post_id is not None
    assert os.path.exists(result.metadata["file_path"])


@pytest.mark.asyncio
async def test_api_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Kairo"


@pytest.mark.asyncio
async def test_api_platforms_and_generate():
    # ASGITransport does not run the app lifespan: create the schema and seed platforms ourselves,
    # otherwise the test silently depends on a developer database that already has them.
    from app.core.init_db import init_db
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Platforms
        p_resp = await ac.get("/api/v1/platforms")
        assert p_resp.status_code == 200
        platforms = p_resp.json()
        assert len(platforms) >= 5

        # Audience segments
        seg_resp = await ac.get("/api/v1/audience/segments")
        assert seg_resp.status_code == 200

        # Generate content
        gen_resp = await ac.post(
            "/api/v1/content/generate",
            json={
                "platform_code": "telegram",
                "topic": "ai_breakthroughs",
                "format_type": "text",
                "tone": "analytical",
                "length_bucket": "short",
            }
        )
        assert gen_resp.status_code == 200
        gen_data = gen_resp.json()
        assert "content_item" in gen_data
        assert gen_data["content_item"]["title"] is not None
        assert len(gen_data["content_item"]["body"]) > 0

        # Dashboard metrics
        dash_resp = await ac.get("/api/v1/dashboard/metrics")
        assert dash_resp.status_code == 200
        dash_data = dash_resp.json()
        # Підпис статусу локалізований, тому перевіряємо наявність, а не літерал
        assert dash_data["system_status"]["status"]
        assert dash_data["system_status"]["decision_engine"]["algorithm"]
