"""
Тести черги публікацій, розкладу, навчання політики з БД та адаптерів на мок-провайдері.

Кожен тест БД працює з окремою тимчасовою SQLite-базою; адаптери ходять у
mock_social через ASGI-транспорт httpx — без мережі й без реальних токенів.
"""

import asyncio
import functools
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.base import PublishResult  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.decision import policy_state  # noqa: E402
from app.decision.factorized_bandit import FactorizedContextualBandit  # noqa: E402
from app.models.entities import (  # noqa: E402
    ConnectedAccount, ContentItem, Platform, PolicyDecision, PolicyReward, Post,
)
from app.scheduler import runner  # noqa: E402
from app.scheduler.planner import next_free_slot  # noqa: E402


@pytest_asyncio.fixture
async def maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_posts(maker, n: int, code: str = "local_archive"):
    async with maker() as db:
        platform = Platform(code=code, display_name=code,
                            adapter_class="app.adapters.local_archive.LocalArchiveAdapter")
        db.add(platform)
        await db.flush()
        account = ConnectedAccount(platform_id=platform.id, external_account_id="acc")
        item = ContentItem(platform_id=platform.id, format="text", tone="analytical",
                           length_bucket="short", body="тест")
        db.add_all([account, item])
        await db.flush()
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add_all([Post(content_item_id=item.id, connected_account_id=account.id,
                         scheduled_at=past, status="pending") for _ in range(n)])
        await db.commit()
        return account.id


# ------------------------------------------------------------------ навчання політики з БД
def test_learn_batch_equals_sequential_updates():
    rng = np.random.RandomState(0)
    samples = []
    for _ in range(30):
        ctx = rng.randn(10).tolist()
        arms = {d: v[rng.randint(len(v))] for d, v in FactorizedContextualBandit(10).dimensions.items()}
        samples.append((ctx, arms, float(rng.rand())))

    seq, batch = FactorizedContextualBandit(10), FactorizedContextualBandit(10)
    for ctx, arms, r in samples:
        seq.update(seq.cascade(ctx, arms), arms, r)
    batch.learn_batch(samples)
    for d in seq.dim_order:
        for arm in seq.heads[d].arm_names:
            assert np.allclose(seq.heads[d].A[arm], batch.heads[d].A[arm])
            assert np.allclose(seq.heads[d].b[arm], batch.heads[d].b[arm])


@pytest.mark.asyncio
async def test_bandit_learns_from_stored_rewards(maker):
    """Винагорода з телеметрії змінює вибір політики (раніше бандит її не бачив)."""
    ctx = [0.5] * 20
    async with maker() as db:
        for i in range(40):
            topic = "developer_humor" if i % 2 else "system_architecture"
            d = PolicyDecision(context_vector=ctx, policy_version=policy_state.POLICY_VERSION,
                               chosen_arms={"topic": topic, "format": "text", "tone": "humorous",
                                            "length": "short", "time_slot": "evening_18_20", "backend": "local"})
            db.add(d)
            await db.flush()
            db.add(PolicyReward(policy_decision_id=d.id, reward=0.1))           # проміжна
            db.add(PolicyReward(policy_decision_id=d.id,                         # остаточна
                                reward=0.9 if topic == "developer_humor" else 0.1,
                                computed_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        await db.commit()
        bandit = await policy_state.rebuild_bandit(db)

    chosen, _, _ = bandit.select(ctx, explore=False)
    assert chosen["topic"] == "developer_humor"
    assert policy_state.learned_decisions() == 40


# ------------------------------------------------------------------ черга публікацій
@pytest.mark.asyncio
async def test_claim_is_exclusive_between_workers(maker):
    await _seed_posts(maker, 6)
    async with maker() as a, maker() as b:
        first, second = await asyncio.gather(runner.claim_due_posts(a, 4), runner.claim_due_posts(b, 4))
    assert not set(first) & set(second)          # жоден допис не захоплено двічі
    assert len(first) + len(second) == 6


class _FailingAdapter:
    async def publish(self, **kwargs):
        return PublishResult(success=False, error_message="мережа недоступна")


@pytest.mark.asyncio
async def test_failed_publication_is_retried_then_marked_failed(maker, monkeypatch):
    await _seed_posts(maker, 1)
    monkeypatch.setattr(runner, "AsyncSessionLocal", maker)
    monkeypatch.setattr(runner, "get_adapter_for_account", lambda account: _FailingAdapter())
    monkeypatch.setattr(settings, "PUBLISH_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "PUBLISH_RETRY_BASE_SECONDS", 0)

    assert await runner.publish_due_posts() == 0
    async with maker() as db:
        post = (await db.execute(select(Post))).scalar_one()
        assert (post.status, post.attempts) == ("pending", 1)   # повтор запланований

    await runner.publish_due_posts()
    async with maker() as db:
        post = (await db.execute(select(Post))).scalar_one()
        assert (post.status, post.attempts) == ("failed", 2)
        assert post.error_message == "мережа недоступна"


@pytest.mark.asyncio
async def test_stale_claim_returns_to_queue(maker, monkeypatch):
    await _seed_posts(maker, 1)
    async with maker() as db:
        assert len(await runner.claim_due_posts(db, 10)) == 1
        post = (await db.execute(select(Post))).scalar_one()
        post.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)  # воркер «упав»
        await db.commit()
        assert len(await runner.claim_due_posts(db, 10)) == 1


# ------------------------------------------------------------------ розклад
def test_manual_schedule_skips_taken_slots():
    now = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)  # понеділок, 10:00 за Києвом
    plan = {"mode": "manual", "utc_offset": 2, "min_gap_minutes": 60,
            "slots": [{"weekday": 0, "time": "09:00"}, {"weekday": 2, "time": "18:30"}]}
    first = next_free_slot(plan, [], now=now)
    assert first == datetime(2026, 11, 11, 16, 30, tzinfo=timezone.utc)   # ср 18:30 місцевого
    second = next_free_slot(plan, [first], now=now)
    assert second == datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)    # наступний пн 09:00


def test_auto_schedule_uses_activity_peaks():
    now = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    activity = [0.1] * 24
    activity[19] = 1.0
    plan = {"mode": "auto", "utc_offset": 2, "per_day": 1}
    assert next_free_slot(plan, [], now=now, activity_hours=activity) == datetime(
        2026, 11, 9, 17, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ адаптери на мок-провайдері
@pytest.fixture
def mock_network(monkeypatch, tmp_path):
    from mock_social import app as mock

    monkeypatch.setattr(mock, "LATENCY", 0.0)
    transport = httpx.ASGITransport(app=mock.app)
    client_cls = functools.partial(httpx.AsyncClient, transport=transport)
    import app.adapters.bluesky as bs
    import app.adapters.facebook as fb
    import app.adapters.instagram as ig
    import app.adapters.mastodon as ms
    import app.adapters.telegram as tg
    import app.adapters.tiktok as tt
    import app.adapters.youtube as yt
    for module in (tg, ms, bs, yt, fb, ig, tt):
        monkeypatch.setattr(module.httpx, "AsyncClient", client_cls)
    monkeypatch.setattr(settings, "TELEGRAM_API_BASE", "http://mock/telegram")
    monkeypatch.setattr(settings, "GRAPH_API_BASE", "http://mock/graph")
    monkeypatch.setattr(settings, "TIKTOK_API_BASE", "http://mock/tiktok")
    monkeypatch.setattr(settings, "GOOGLE_API_BASE", "http://mock/google")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_TOKEN_URL", "http://mock/google/token")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://kairo.test")
    image = tmp_path / "keyframe_1.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-mp4")
    return {
        "image": str(image), "image_url": "/media/1/keyframe_1.jpg",
        "video": str(video), "video_url": "/media/1/video.mp4",
    }


ADAPTER_CASES = [
    ("telegram", {"bot_token": "123:abc", "chat_id": "@mock"}, "image_post"),
    ("mastodon", {"instance_url": "http://mock/mastodon", "access_token": "t"}, "image_post"),
    ("bluesky", {"handle": "hw.mock", "app_password": "p", "service": "http://mock/bluesky"}, "image_post"),
    ("youtube", {"client_id": "c", "client_secret": "s", "refresh_token": "r"}, "video_short"),
    ("facebook", {"page_id": "777", "page_access_token": "t"}, "image_post"),
    ("instagram", {"ig_user_id": "888", "access_token": "t"}, "image_post"),
    ("tiktok", {"access_token": "t"}, "video_short"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("code,creds,fmt", ADAPTER_CASES)
async def test_adapter_publishes_and_reads_metrics(mock_network, code, creds, fmt):
    from app.adapters.registry import BUILTIN_ADAPTERS

    adapter = BUILTIN_ADAPTERS[code](creds)
    media = dict(mock_network) if fmt == "video_short" else {
        k: v for k, v in mock_network.items() if "video" not in k}
    result = await adapter.publish(1, "Заголовок", "Текст допису", media, ["ai"], fmt)
    assert result.success, result.error_message
    assert result.external_post_id and "sim" not in result.external_post_id

    if code == "telegram":
        counters = await adapter.fetch_channel_counters()
        assert result.external_post_id in counters
    else:
        metrics = await adapter.fetch_post_metrics(result.external_post_id)
        assert set(metrics) >= {"reactions", "comments"}


@pytest.mark.asyncio
async def test_instagram_rejects_text_only(mock_network):
    from app.adapters.instagram import InstagramAdapter

    result = await InstagramAdapter({"ig_user_id": "1", "access_token": "t"}).publish(
        1, None, "лише текст", {}, [], "text")
    assert not result.success and "photos or videos" in result.error_message


@pytest.mark.asyncio
async def test_interactions_feed_audience_profiles(mock_network):
    from app.adapters.mastodon import MastodonAdapter

    from mock_social import app as mock

    adapter = MastodonAdapter({"instance_url": "http://mock/mastodon", "access_token": "t"})
    result = await adapter.publish(1, "t", "b", {}, [], "text")
    mock.POSTS[f"mastodon:{result.external_post_id}"]["created"] -= 24 * 3600 / mock.TIME_SCALE  # «добу тому»
    interactions = await adapter.fetch_interactions(datetime.now(timezone.utc) - timedelta(days=1))
    assert interactions
    assert all(i.external_user_id.endswith("@mock.social") for i in interactions)
    assert {i.external_post_id for i in interactions} == {result.external_post_id}
