"""
Тести збору телеметрії.

Мережа тут не потрібна: перевіряється саме та логіка, через яку проходять усі
платформи — переведення абсолютних лічильників у приріст і нормування ваг
винагороди під сигнали, доступні конкретній мережі.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.adapters.mastodon import MastodonAdapter  # noqa: E402
from app.adapters.telegram import TelegramAdapter  # noqa: E402
from app.adapters.youtube import YouTubeAdapter  # noqa: E402
from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.models.entities import (  # noqa: E402
    ConnectedAccount,
    ContentItem,
    Platform,
    Post,
    TelemetryEvent,
)
from app.telemetry.collector import _apply_counters, _recorded_totals  # noqa: E402


@pytest_asyncio.fixture
async def db_session(tmp_path):
    """
    Власний рушій і окрема тимчасова база на кожен тест.

    Спільний рушій застосунку прив’язується до першого циклу подій, а робоча
    база розробника може бути старішої схеми — тест не повинен від неї залежати.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'telemetry.db'}")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await test_engine.dispose()


def test_platform_capabilities_are_declared_honestly():
    """Можливості оголошені там само, де реалізація, і збігаються з фактом API."""
    assert "views" not in MastodonAdapter.COUNTER_METRICS  # Mastodon переглядів не віддає
    assert MastodonAdapter.SUPPORTS_PER_ACTOR is True

    assert TelegramAdapter.COUNTER_METRICS == ["reactions"]
    assert TelegramAdapter.SUPPORTS_PER_ACTOR is False  # реакції в каналі анонімні

    assert YouTubeAdapter.AVAILABLE is True
    assert "views" in YouTubeAdapter.COUNTER_METRICS  # Data API віддає viewCount


@pytest.mark.asyncio
async def test_counters_are_converted_to_increments(db_session):
    """
    Лічильники приходять абсолютними, а зберігається приріст.

    Повторний опит із тим самим значенням не має додавати телеметрію — інакше
    кожен цикл воркера подвоював би винагороду.
    """
    platform = Platform(
        code=f"test_{datetime.now().timestamp()}",
        display_name="Тестова мережа",
        adapter_class="app.adapters.local_archive.LocalArchiveAdapter",
        config_schema={},
    )
    db_session.add(platform)
    await db_session.flush()

    account = ConnectedAccount(
        platform_id=platform.id,
        external_account_id="test_account",
        credentials_encrypted="",
        status="active",
        connected_at=datetime.now(timezone.utc),
    )
    item = ContentItem(
        platform_id=platform.id,
        format="text",
        tone="analytical",
        length_bucket="medium",
        body="тестовий матеріал",
        status="published",
    )
    db_session.add_all([account, item])
    await db_session.flush()

    post = Post(
        content_item_id=item.id,
        connected_account_id=account.id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
        published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        external_post_id="ext-1",
        status="published",
    )
    db_session.add(post)
    await db_session.flush()

    written = await _apply_counters(
        db_session, post, platform.id, {"reactions": 5, "comments": 2}, "test"
    )
    await db_session.flush()
    assert written == 2

    totals = await _recorded_totals(db_session, post.id)
    assert totals["reaction"] == 5
    assert totals["comment"] == 2

    # Той самий стан лічильників — нових подій бути не повинно
    written_again = await _apply_counters(
        db_session, post, platform.id, {"reactions": 5, "comments": 2}, "test"
    )
    await db_session.flush()
    assert written_again == 0

    # Приріст записується як різниця, а не як нове абсолютне значення
    await _apply_counters(db_session, post, platform.id, {"reactions": 9}, "test")
    await db_session.flush()
    totals = await _recorded_totals(db_session, post.id)
    assert totals["reaction"] == 9

    events = (
        await db_session.execute(
            TelemetryEvent.__table__.select().where(TelemetryEvent.post_id == post.id)
        )
    ).all()
    reaction_values = sorted(
        float(e.event_value) for e in events if e.event_type == "reaction"
    )
    assert reaction_values == [4.0, 5.0]  # 5 спочатку, потім приріст 4

    await db_session.rollback()
